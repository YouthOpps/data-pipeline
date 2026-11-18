import fs from 'node:fs/promises';
import { loadData } from '../../qa-website/scripts/data.mjs';

let input = '';
for await (const part of process.stdin) input += part;
const { records, metadata } = JSON.parse(input);
const source = 'cz-charles-university';
if (metadata.source !== source || records.some(record => record.source !== source)) {
  throw new Error('Consumer input source differs');
}

const root = '/ram/datas';
const files = new Map([
  [`${root}/${source}/data.json`, JSON.stringify(records)],
  [`${root}/${source}/metadata.json`, JSON.stringify(metadata)],
]);
fs.lstat = async name => {
  if (name !== root && !files.has(name)) throw new Error('Unexpected consumer path');
  return { isSymbolicLink: () => false };
};
fs.readdir = async name => {
  if (name !== root) throw new Error('Unexpected consumer directory');
  return [{ name: source, isSymbolicLink: () => false, isDirectory: () => true }];
};
fs.readFile = async name => {
  if (!files.has(name)) throw new Error('Missing consumer input');
  return files.get(name);
};

const result = await loadData(root);
const ids = result.opportunities.map(record => record.id).sort();
const expected = records.map(record => record.id).sort();
if (JSON.stringify(ids) !== JSON.stringify(expected)) throw new Error('Consumer IDs differ');
console.log(JSON.stringify({ ids }));
