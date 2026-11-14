import { createServer } from "vite";
import { execFile } from "node:child_process";
import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const server = await createServer({
  root,
  server: { host: "127.0.0.1", port: 5185, strictPort: true },
  logLevel: "error",
});
await server.listen();
const shots = process.env.SHOTS_DIR;
if (shots) await fs.mkdir(shots, { recursive: true });
try {
  for (const script of [
    "verify-auth.mjs",
    "verify-me.mjs",
    "verify-rewards.mjs",
  ]) {
    await new Promise((resolve, reject) =>
      execFile(
        process.execPath,
        [path.join(root, "scripts", script)],
        {
          cwd: root,
          env: {
            ...process.env,
            CHROME_PATH:
              process.env.CHROME_PATH ||
              (process.platform === "win32"
                ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
                : undefined),
            BASE_URL: "http://127.0.0.1:5185",
            OUT_DIR: shots ? path.join(shots, script.replace(".mjs", "")) : process.env.OUT_DIR,
          },
          windowsHide: true,
          encoding: "utf8",
        },
        (error, stdout, stderr) => {
          process.stdout.write(stdout);
          process.stderr.write(stderr);
          if (error) reject(error);
          else resolve();
        },
      ),
    );
    console.log("PASS " + script);
  }
} finally {
  await server.close();
}
