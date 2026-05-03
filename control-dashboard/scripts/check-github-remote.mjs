import { execSync } from "node:child_process";

let url;
try {
  url = execSync("git remote get-url origin", {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  }).trim();
} catch {
  console.error(
    "No git remote named `origin`. The gh-pages tool needs it to know where to push.\n" +
      "From the repository root, run (edit URL to match your GitHub repo):\n" +
      "  git remote add origin https://github.com/mxl983/rover.git",
  );
  process.exit(1);
}

console.log("gh-pages will push the `gh-pages` branch to:", url);
