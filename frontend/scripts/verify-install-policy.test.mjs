// @vitest-environment node

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { validateInstallPolicy } from "./verify-install-policy.mjs";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
let valid;

function clone(value) {
  return structuredClone(value);
}

beforeAll(async () => {
  const [packageText, lockText, npmrc] = await Promise.all([
    readFile(resolve(frontendRoot, "package.json"), "utf8"),
    readFile(resolve(frontendRoot, "package-lock.json"), "utf8"),
    readFile(resolve(frontendRoot, ".npmrc"), "utf8"),
  ]);
  valid = {
    actualNpmVersion: "11.17.0",
    lockData: JSON.parse(lockText),
    npmrc,
    packageData: JSON.parse(packageText),
    pendingData: { allowScripts: [] },
  };
});

describe("frontend install-script admission policy", () => {
  it("binds strict policy to exact npm, package versions and registry integrities", () => {
    const result = validateInstallPolicy(clone(valid));

    expect(result.status).toBe("PASS");
    expect(result.pendingInstallScriptCount).toBe(0);
    expect(result.reviewedInstallScripts).toHaveLength(2);
    expect(result.contentSha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it.each([
    ["npm drift", (input) => (input.actualNpmVersion = "11.18.0")],
    [
      "strict policy disabled",
      (input) => (input.npmrc = input.npmrc.replace("strict-allow-scripts=true", "strict-allow-scripts=false")),
    ],
    [
      "unpinned approval",
      (input) => {
        input.packageData.allowScripts.esbuild = true;
        delete input.packageData.allowScripts["esbuild@0.25.12"];
      },
    ],
    [
      "lock integrity drift",
      (input) => (input.lockData.packages["node_modules/esbuild"].integrity = "sha512:tampered"),
    ],
    [
      "new install script",
      (input) => {
        input.lockData.packages["node_modules/unreviewed"] = {
          hasInstallScript: true,
          integrity: "sha512:unreviewed",
          resolved: "https://registry.npmjs.org/unreviewed/-/unreviewed-1.0.0.tgz",
          version: "1.0.0",
        };
      },
    ],
    ["pending script", (input) => input.pendingData.allowScripts.push({ name: "new" })],
  ])("rejects %s", (_label, mutation) => {
    const input = clone(valid);
    mutation(input);

    expect(() => validateInstallPolicy(input)).toThrow();
  });
});
