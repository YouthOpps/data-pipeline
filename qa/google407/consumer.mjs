import fs from 'node:fs/promises';
import {loadData} from '../../qa-website/scripts/data.mjs';
let input = '';
for await (const part of process.stdin) input += part;
const value = JSON.parse(input);
const source = 'google';
if (value.metadata.source !== source ||
    value.records.some(record => record.source !== source)) {
  throw new Error('Observer source identity mismatch');
}
const root = '/ram/datas';
const files = new Map([
  [`${root}/${source}/data.json`, JSON.stringify(value.records)],
  [`${root}/${source}/metadata.json`, JSON.stringify(value.metadata)],
]);
fs.lstat = async file => {
  if (file !== root && !files.has(file)) throw Error('Unexpected path');
  return {isSymbolicLink: () => false};
};
fs.readdir = async file => {
  if (file !== root) throw Error('Unexpected root');
  return [{name: source, isSymbolicLink: () => false, isDirectory: () => true}];
};
fs.readFile = async file => {
  if (!files.has(file)) throw Error('Missing input');
  return files.get(file);
};
const result = await loadData(root);
const expected = value.records.map(record => record.id).sort();
const actual = result.opportunities.map(record => record.id).sort();
if (JSON.stringify(expected) !== JSON.stringify(actual)) {
  throw Error('Incomplete consumer ID coverage');
}
console.log(JSON.stringify({count: actual.length, ids: actual,
  sources: result.sources.map(source => source.id)}));
