// @vitest-environment node

import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  cleanOutput,
  createBuildContract,
  prepareOutput,
  verifyOutput,
} from "./clean-build.mjs";

const temporaryDirectories = [];
const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function sourceFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await sourceFiles(path)));
    else if (entry.isFile() && /\.(?:ts|tsx)$/.test(entry.name)) files.push(path);
  }
  return files;
}

async function temporaryOutput() {
  const directory = await mkdtemp(resolve(tmpdir(), "rag-frontend-build-"));
  temporaryDirectories.push(directory);
  return directory;
}

async function writeValidOutput(directory) {
  await mkdir(resolve(directory, "app"), { recursive: true });
  await writeFile(
    resolve(directory, "index.html"),
    '<script type="module" src="/app/index-12345678.js"></script>' +
      '<link rel="stylesheet" href="/app/index-abcdefgh.css">',
  );
  await writeFile(resolve(directory, "app/index-12345678.js"), "export{};");
  await writeFile(resolve(directory, "app/index-abcdefgh.css"), ":root{}");
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      cleanOutput(directory).then(async () => {
        const { rm } = await import("node:fs/promises");
        await rm(directory, { recursive: true, force: true });
      }),
    ),
  );
});

describe("frontend clean build contract", () => {
  it("removes every previous generated asset without touching legacy files", async () => {
    const output = await temporaryOutput();
    await mkdir(resolve(output, "app/nested"), { recursive: true });
    await mkdir(resolve(output, "assets"), { recursive: true });
    await writeFile(resolve(output, "app/old-12345678.js"), "old");
    await writeFile(resolve(output, "app/nested/old-abcdefgh.css"), "old");
    await writeFile(resolve(output, "index.html"), "old index");
    await writeFile(resolve(output, "build-contract.json"), "{}");
    await writeFile(resolve(output, "assets/legacy.js"), "legacy");

    await cleanOutput(output);

    expect(await stat(resolve(output, "app")).catch(() => null)).toBeNull();
    expect(await stat(resolve(output, "index.html")).catch(() => null)).toBeNull();
    expect(
      await stat(resolve(output, "build-contract.json")).catch(() => null),
    ).toBeNull();
    expect(await readFile(resolve(output, "assets/legacy.js"), "utf8")).toBe("legacy");
  });

  it("writes the same source-to-artifact contract for identical output", async () => {
    const output = await temporaryOutput();
    await prepareOutput(output);
    await writeValidOutput(output);

    const first = await verifyOutput(output);
    const firstFile = await readFile(resolve(output, "build-contract.json"), "utf8");
    const second = await verifyOutput(output);
    const secondFile = await readFile(resolve(output, "build-contract.json"), "utf8");

    expect(second).toEqual(first);
    expect(secondFile).toBe(firstFile);
    expect(first.buildSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(
      await stat(resolve(output, ".frontend-source.sha256")).catch(() => null),
    ).toBeNull();
    expect(Object.keys(first.artifacts)).toEqual([
      "index.html",
      "app/index-12345678.js",
      "app/index-abcdefgh.css",
    ]);
  });

  it("rejects non-hashed assets and local backend fallbacks", async () => {
    const output = await temporaryOutput();
    await writeValidOutput(output);
    await writeFile(resolve(output, "app/chunk.js"), "export{};");

    await expect(createBuildContract(output)).rejects.toThrow("non-hashed asset");

    await cleanOutput(output);
    await writeValidOutput(output);
    await writeFile(
      resolve(output, "app/index-12345678.js"),
      'fetch("http://127.0.0.1:8000/mcp")',
    );
    await expect(createBuildContract(output)).rejects.toThrow(
      "production invariant failed",
    );
  });

  it("keeps the React Router advisory surface client-only without RSC or SSR actions", async () => {
    const paths = await sourceFiles(resolve(frontendRoot, "src"));
    const sources = await Promise.all(paths.map((path) => readFile(path, "utf8")));
    const joined = sources.join("\n");
    const main = await readFile(resolve(frontendRoot, "src/main.tsx"), "utf8");

    expect(main).toContain("HashRouter");
    expect(joined).not.toMatch(
      /react-router(?:-dom)?\/(?:server|rsc)|createStaticRouter|StaticRouterProvider|ServerRouter|RSCStaticRouter/,
    );
    expect(joined).not.toMatch(/\b(?:clientAction|serverAction|unstable_RSCStaticRouter)\b/);
  });
});
