import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import {pathToFileURL} from 'node:url'
import {createHash} from 'node:crypto'
import process from 'node:process'

const [consumerPath, source] = process.argv.slice(2)
assert(consumerPath && source)
const inputChunks = []
for await (const chunk of process.stdin) inputChunks.push(chunk)
const envelope = JSON.parse(Buffer.concat(inputChunks))
const {loadData} = await import(pathToFileURL(consumerPath).href)
const virtual = '/virtual-ens-selection/datas'
const before = {lstat: fs.lstat, readdir: fs.readdir, readFile: fs.readFile}

async function consume(records, metadata) {
  assert.equal(metadata.source, source)
  assert.equal(metadata.record_count, records.length)
  assert(records.every(record => record.source === source && record.id.startsWith(`${source}-`)))
  const files = new Map([
    [`${virtual}/${source}/data.json`, JSON.stringify(records)],
    [`${virtual}/${source}/metadata.json`, JSON.stringify(metadata)],
  ])
  fs.lstat = async path => {
    if (path === virtual || files.has(path)) return {isSymbolicLink: () => false}
    return before.lstat(path)
  }
  fs.readdir = async (path, ...options) => path === virtual ? [{
    name: source, isSymbolicLink: () => false, isDirectory: () => true,
  }] : before.readdir(path, ...options)
  fs.readFile = async (path, ...options) => files.has(path) ? files.get(path) : before.readFile(path, ...options)
  const result = await loadData(virtual)
  assert.deepEqual(result.opportunities.map(record => record.id).sort(), records.map(record => record.id).sort())
  assert.equal(result.sources.length, 1)
  assert.equal(result.sources[0].id, source)
  return result.opportunities.length
}
try {
  const raw = await consume(envelope.raw, envelope.rawMetadata)
  const filtered = await consume(envelope.filtered, envelope.filteredMetadata)
  const expired = await consume(envelope.expired, envelope.expiredMetadata)
  await assert.rejects(consume(envelope.raw.map(record => ({...record, source: 'wrong'})), envelope.rawMetadata))
  const consumerSHA = createHash('sha256').update(await before.readFile(consumerPath)).digest('hex')
  process.stdout.write(JSON.stringify({raw, filtered, expired, wrong_source_rejected: true, consumer_sha256: consumerSHA}))
} finally {
  Object.assign(fs, before)
}
