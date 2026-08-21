import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

if (process.platform === "win32") {
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const target = path.join(
    projectRoot,
    "node_modules",
    "vinext",
    "dist",
    "server",
    "static-file-cache.js",
  );
  const before = 'relativePath: path.relative(base, batch[j]),';
  const after = 'relativePath: path.relative(base, batch[j]).split(path.sep).join("/"),';
  const source = fs.readFileSync(target, "utf8");
  if (source.includes(before)) fs.writeFileSync(target, source.replace(before, after));
  else if (!source.includes(after)) throw new Error("Unsupported vinext static cache layout");
}
