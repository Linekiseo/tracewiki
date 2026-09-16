import { createHash } from "node:crypto";
import {
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { dirname, isAbsolute, parse, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptFile = fileURLToPath(import.meta.url);
const frontendRoot = resolve(dirname(scriptFile), "..");
const defaultOutputDirectory = resolve(frontendRoot, "../web");
const assetsDirectoryName = "app";
const contractFileName = "build-contract.json";
const sourceSnapshotFileName = ".frontend-source.sha256";
const hashedAsset =
  /-[A-Za-z0-9_-]{8}\.(?:css|js|png|webp|svg|ico|woff2?)$/;
const forbiddenProductionValue =
  /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):8000|\/\/# sourceMappingURL=|\/\*# sourceMappingURL=/i;

function normalizedPath(path) {
  return path.split(sep).join("/");
}

function outputDirectoryFromEnvironment() {
  const configured = process.env.RAG_FRONTEND_OUT_DIR;
  return configured
    ? resolve(frontendRoot, configured)
    : defaultOutputDirectory;
}

function assertSafeOutputDirectory(outputDirectory) {
  const resolved = resolve(outputDirectory);
  const unsafeDirectories = new Set([
    parse(resolved).root,
    frontendRoot,
    resolve(frontendRoot, ".."),
  ]);
  if (unsafeDirectories.has(resolved)) {
    throw new Error(`Refusing to clean unsafe frontend output directory: ${resolved}`);
  }
}

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listFiles(path)));
    else if (entry.isFile()) files.push(path);
  }
  return files.sort();
}

async function digestFiles(root, files) {
  const digest = createHash("sha256");
  for (const file of files) {
    const path = normalizedPath(relative(root, file));
    digest.update(path);
    digest.update("\0");
    digest.update(await readFile(file));
    digest.update("\0");
  }
  return digest.digest("hex");
}

async function sourceFiles() {
  const sourceDirectory = resolve(frontendRoot, "src");
  const files = (await listFiles(sourceDirectory)).filter((file) => {
    const path = normalizedPath(relative(sourceDirectory, file));
    return !path.includes(".test.") && !path.startsWith("test/");
  });
  const fixedInputs = [
    "index.html",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "scripts/clean-build.mjs",
  ].map((path) => resolve(frontendRoot, path));

  for (const file of fixedInputs) {
    if (!(await stat(file).catch(() => null))?.isFile()) {
      throw new Error(`Missing deterministic frontend build input: ${file}`);
    }
  }
  return [...files, ...fixedInputs].sort();
}

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

export async function cleanOutput(
  outputDirectory = outputDirectoryFromEnvironment(),
) {
  assertSafeOutputDirectory(outputDirectory);
  await Promise.all([
    rm(resolve(outputDirectory, assetsDirectoryName), {
      recursive: true,
      force: true,
    }),
    rm(resolve(outputDirectory, "index.html"), { force: true }),
    rm(resolve(outputDirectory, contractFileName), { force: true }),
    rm(resolve(outputDirectory, sourceSnapshotFileName), { force: true }),
  ]);
}

async function currentSourceSha256() {
  return digestFiles(frontendRoot, await sourceFiles());
}

export async function prepareOutput(
  outputDirectory = outputDirectoryFromEnvironment(),
) {
  await cleanOutput(outputDirectory);
  await mkdir(outputDirectory, { recursive: true });
  const sourceSha256 = await currentSourceSha256();
  await writeFile(
    resolve(outputDirectory, sourceSnapshotFileName),
    `${sourceSha256}\n`,
    "utf8",
  );
  return sourceSha256;
}

export async function createBuildContract(
  outputDirectory = outputDirectoryFromEnvironment(),
  expectedSourceSha256,
) {
  const resolvedOutput = resolve(outputDirectory);
  const indexPath = resolve(resolvedOutput, "index.html");
  const assetRoot = resolve(resolvedOutput, assetsDirectoryName);
  const index = await readFile(indexPath, "utf8").catch(() => {
    throw new Error(`Frontend build did not create ${indexPath}`);
  });
  const assetFiles = await listFiles(assetRoot);

  if (!assetFiles.length) {
    throw new Error(`Frontend build did not create assets in ${assetRoot}`);
  }

  const referencedAssets = new Set(
    [...index.matchAll(/(?:src|href)="\/?app\/([^"]+)"/g)].map(
      (match) => match[1],
    ),
  );
  if (!referencedAssets.size) {
    throw new Error("Frontend index does not reference any hashed app assets");
  }

  for (const reference of referencedAssets) {
    const target = resolve(assetRoot, reference);
    if (!target.startsWith(`${assetRoot}${sep}`)) {
      throw new Error(`Frontend index contains an unsafe asset path: ${reference}`);
    }
    if (!(await stat(target).catch(() => null))?.isFile()) {
      throw new Error(`Frontend index references a missing asset: app/${reference}`);
    }
  }

  const artifacts = {};
  for (const file of [indexPath, ...assetFiles]) {
    const path = normalizedPath(relative(resolvedOutput, file));
    const content = await readFile(file);
    if (path.startsWith(`${assetsDirectoryName}/`)) {
      const name = path.slice(assetsDirectoryName.length + 1);
      if (!hashedAsset.test(name) || name.endsWith(".map")) {
        throw new Error(`Frontend build emitted a non-hashed asset: ${path}`);
      }
    }
    if (forbiddenProductionValue.test(content.toString("utf8"))) {
      throw new Error(`Frontend production invariant failed in ${path}`);
    }
    artifacts[path] = {
      bytes: content.byteLength,
      sha256: sha256(content),
    };
  }

  const sourceSha256 = await currentSourceSha256();
  if (expectedSourceSha256 && sourceSha256 !== expectedSourceSha256) {
    throw new Error(
      "Frontend build inputs changed while the production bundle was being created",
    );
  }
  const artifactsSha256 = sha256(JSON.stringify(artifacts));
  return {
    schemaVersion: 1,
    sourceSha256,
    artifactsSha256,
    buildSha256: sha256(`${sourceSha256}\0${artifactsSha256}`),
    artifacts,
  };
}

export async function verifyOutput(
  outputDirectory = outputDirectoryFromEnvironment(),
) {
  const sourceSnapshotPath = resolve(outputDirectory, sourceSnapshotFileName);
  const expectedSourceSha256 = await readFile(sourceSnapshotPath, "utf8")
    .then((value) => value.trim())
    .catch(() => undefined);
  const contract = await createBuildContract(outputDirectory, expectedSourceSha256);
  await mkdir(outputDirectory, { recursive: true });
  await writeFile(
    resolve(outputDirectory, contractFileName),
    `${JSON.stringify(contract, null, 2)}\n`,
    "utf8",
  );
  await rm(sourceSnapshotPath, { force: true });
  return contract;
}

async function main() {
  const command = process.argv[2] || "clean";
  const outputDirectory = outputDirectoryFromEnvironment();
  if (command === "clean") {
    await cleanOutput(outputDirectory);
    return;
  }
  if (command === "prepare") {
    await prepareOutput(outputDirectory);
    return;
  }
  if (command === "verify") {
    await verifyOutput(outputDirectory);
    return;
  }
  throw new Error(`Unknown frontend build command: ${command}`);
}

if (isAbsolute(process.argv[1] || "") && resolve(process.argv[1]) === scriptFile) {
  await main();
}
