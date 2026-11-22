import fs from 'node:fs/promises';
import {loadData} from '../_qa/website/scripts/data.mjs';
let serialized='';
for await (const chunk of process.stdin) serialized+=chunk;
const input=JSON.parse(serialized);
const root='/ram/datas';
const folder=`${root}/dk-fulbright-center`;
fs.lstat=async file=>{if (file!==root&&!file.startsWith(folder+'/')) throw Error('bad input');return {isSymbolicLink:()=>false}};
fs.readdir=async file=>{if(file!==root)throw Error('bad source root');return [{name:'dk-fulbright-center',isSymbolicLink:()=>false,isDirectory:()=>true}]};
fs.readFile=async file=>{
  if (file===`${folder}/data.json`)return JSON.stringify(input.records);
  if (file===`${folder}/metadata.json`)return JSON.stringify(input.metadata);
  throw Error('bad dataset request');
};
const data=await loadData(root);
const expected=input.records.map(record=>record.id).sort();
const actual=data.opportunities.map(record=>record.id).sort();
if (JSON.stringify(actual)!==JSON.stringify(expected)) throw Error('Website candidate identity changed');
console.log(JSON.stringify({count:actual.length,ids:actual,sources:data.sources.map(s=>s.id)}));
