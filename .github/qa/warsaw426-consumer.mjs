import fs from 'node:fs/promises';
import {loadData} from '../../_qa/website/scripts/data.mjs';
let input='';
for await (const part of process.stdin) input += part;
const envelope=JSON.parse(input);
const source='pl-warsaw-tech-msc-admissions';
if (envelope.metadata.source !== source || envelope.records.some(record=>record.source!==source)) {
  throw new Error('Observer source identity mismatch');
}
const root='/ram/datas';
const files=new Map([
  [`${root}/${source}/data.json`,JSON.stringify(envelope.records)],
  [`${root}/${source}/metadata.json`,JSON.stringify(envelope.metadata)],
]);
fs.lstat=async file=>{
  if(file !== root && !files.has(file)) throw new Error('Unexpected observer path');
  return {isSymbolicLink:()=>false};
};
fs.readdir=async file=>{
  if(file !== root) throw new Error('Unexpected observer root');
  return [{name:source,isSymbolicLink:()=>false,isDirectory:()=>true}];
};
fs.readFile=async file=>{
  if(!files.has(file)) throw new Error('Missing observer input');
  return files.get(file);
};
const result=await loadData(root);
const expected=envelope.records.map(record=>record.id).sort();
const actual=result.opportunities.map(record=>record.id).sort();
if(JSON.stringify(expected)!==JSON.stringify(actual)) throw new Error('Observer ID mismatch');
console.log(JSON.stringify({count:actual.length,ids:actual,sources:result.sources.map(s=>s.id)}));
