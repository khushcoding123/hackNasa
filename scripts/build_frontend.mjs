// Type-check, bundle the app, and copy CesiumJS runtime files (workers, assets, widget styles).
// No Cesium ion credentials are needed: only the app's own terrain endpoint and public WMS imagery are used.
import {build} from "esbuild";
import {cpSync, mkdirSync, rmSync} from "node:fs";
import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const run = (...args) => execFileSync(process.execPath, args, {cwd: root, stdio: "inherit"});

run(path.join(root, "node_modules/typescript/bin/tsc"), "-p", "tsconfig.json", "--noEmit");

await build({
  entryPoints: [path.join(root, "frontend/app.ts"), path.join(root, "frontend/ora.ts")],
  bundle: true, format: "esm", target: "es2022", minify: true, legalComments: "inline",
  outdir: path.join(root, "web"),
  logLevel: "warning",
});

const source = path.join(root, "node_modules/cesium/Build/Cesium"), target = path.join(root, "web/cesium");
rmSync(target, {recursive: true, force: true});
mkdirSync(target, {recursive: true});
for (const folder of ["Workers", "ThirdParty", "Assets", "Widgets"]) cpSync(path.join(source, folder), path.join(target, folder), {recursive: true});
console.log("Built web/app.js, web/ora.js and web/cesium");
