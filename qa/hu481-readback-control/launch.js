const { spawnSync } = require("node:child_process");
const path = require("node:path");

const result = spawnSync("python", ["-B", path.join(__dirname, "runner.py")], {
  stdio: "inherit",
  env: { ...process.env, HU481_NODE: process.execPath },
});
process.exitCode = result.status ?? 1;
