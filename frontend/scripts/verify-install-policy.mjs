import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptFile = fileURLToPath(import.meta.url);
const frontendRoot = resolve(dirname(scriptFile), "..");

const expectedNpmVersion = "11.17.0";
const expectedNpmrc = {
  audit: "true",
  "engine-strict": "true",
  "strict-allow-scripts": "true",
};
const expectedInstallScripts = {
  "node_modules/esbuild": {
    approval: "esbuild@0.25.12",
    integrity:
      "sha512-bbPBYYrtZbkt6Os6FiTLCTFxvq4tt3JKall1vRwshA3fdVztsLAatFaZobhkBC8/BrPetoa0oksYoKXoG4ryJg==",
    resolved: "https://registry.npmjs.org/esbuild/-/esbuild-0.25.12.tgz",
    version: "0.25.12",
  },
  "node_modules/fsevents": {
    approval: "fsevents@2.3.3",
    integrity:
      "sha512-5xoDfX+fL7faATnagmWPpbFtwh/R77WmMMqqHGS65C3vvB0YHrgF+B1YmZ3441tMj5n63k0212XNoJwzlhffQw==",
    resolved: "https://registry.npmjs.org/fsevents/-/fsevents-2.3.3.tgz",
    version: "2.3.3",
  },
};

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function parseNpmrc(value) {
  const result = {};
  for (const originalLine of value.split(/\r?\n/)) {
    const line = originalLine.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) throw new Error(`invalid .npmrc line: ${originalLine}`);
    const key = line.slice(0, separator).trim();
    const setting = line.slice(separator + 1).trim();
    if (Object.hasOwn(result, key)) throw new Error(`duplicate .npmrc key: ${key}`);
    result[key] = setting;
  }
  return result;
}

function assertExactObject(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} does not match the reviewed policy`);
  }
}

export function validateInstallPolicy({
  actualNpmVersion,
  lockData,
  npmrc,
  packageData,
  pendingData,
}) {
  if (actualNpmVersion !== expectedNpmVersion) {
    throw new Error(
      `npm version ${actualNpmVersion} is not the reviewed ${expectedNpmVersion}`,
    );
  }
  if (packageData.packageManager !== `npm@${expectedNpmVersion}`) {
    throw new Error("packageManager is not pinned to the reviewed npm version");
  }
  if (packageData.engines?.npm !== expectedNpmVersion) {
    throw new Error("engines.npm is not pinned to the reviewed npm version");
  }
  assertExactObject(parseNpmrc(npmrc), expectedNpmrc, ".npmrc");

  const expectedApprovals = Object.fromEntries(
    Object.values(expectedInstallScripts)
      .map(({ approval }) => [approval, true])
      .sort(([left], [right]) => left.localeCompare(right)),
  );
  assertExactObject(
    packageData.allowScripts,
    expectedApprovals,
    "allowScripts",
  );

  if (lockData.lockfileVersion !== 3 || lockData.requires !== true) {
    throw new Error("package-lock must remain a required lockfileVersion 3 graph");
  }
  const installScriptPaths = Object.entries(lockData.packages ?? {})
    .filter(([, row]) => row?.hasInstallScript === true)
    .map(([path]) => path)
    .sort();
  assertExactObject(
    installScriptPaths,
    Object.keys(expectedInstallScripts).sort(),
    "install-script dependency set",
  );

  for (const [path, expected] of Object.entries(expectedInstallScripts)) {
    const row = lockData.packages[path];
    if (!row || row.hasInstallScript !== true) {
      throw new Error(`${path} is missing its reviewed install-script marker`);
    }
    for (const field of ["version", "resolved", "integrity"]) {
      if (row[field] !== expected[field]) {
        throw new Error(`${path} ${field} does not match the reviewed lock identity`);
      }
    }
  }

  if (pendingData !== undefined) {
    if (
      !pendingData ||
      !Array.isArray(pendingData.allowScripts) ||
      pendingData.allowScripts.length !== 0
    ) {
      throw new Error("npm reports unreviewed install scripts after locked install");
    }
  }

  const reviewed = Object.entries(expectedInstallScripts).map(([path, value]) => ({
    approval: value.approval,
    integrity: value.integrity,
    path,
    resolved: value.resolved,
  }));
  const policy = {
    actualNpmVersion,
    lockfileVersion: lockData.lockfileVersion,
    pendingInstallScriptCount: pendingData?.allowScripts?.length ?? null,
    reviewedInstallScripts: reviewed,
    schemaVersion: "rag-frontend-install-policy-v1",
    status: "PASS",
    strictAllowScripts: true,
  };
  return {
    ...policy,
    contentSha256: sha256(JSON.stringify(policy)),
  };
}

async function main() {
  const pendingIndex = process.argv.indexOf("--pending");
  if (pendingIndex >= 0 && !process.argv[pendingIndex + 1]) {
    throw new Error("--pending requires a JSON path");
  }
  const [packageText, lockText, npmrc, pendingText] = await Promise.all([
    readFile(resolve(frontendRoot, "package.json"), "utf8"),
    readFile(resolve(frontendRoot, "package-lock.json"), "utf8"),
    readFile(resolve(frontendRoot, ".npmrc"), "utf8"),
    pendingIndex >= 0
      ? readFile(resolve(process.cwd(), process.argv[pendingIndex + 1]), "utf8")
      : undefined,
  ]);
  const result = validateInstallPolicy({
    actualNpmVersion: execFileSync("npm", ["--version"], {
      encoding: "utf8",
    }).trim(),
    lockData: JSON.parse(lockText),
    npmrc,
    packageData: JSON.parse(packageText),
    pendingData: pendingText === undefined ? undefined : JSON.parse(pendingText),
  });
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (resolve(process.argv[1] || "") === scriptFile) await main();
