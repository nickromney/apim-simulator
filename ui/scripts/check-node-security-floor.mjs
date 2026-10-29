const minimumByMajor = new Map([
  [22, [23, 4]],
  [24, [22, 1]],
  [26, [12, 1]],
]);

const version = process.argv[2] ?? process.versions.node;
const match = /^(22|24|26)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(version);

if (!match) {
  console.error(`Unsupported or prerelease Node version: ${version}`);
  process.exit(1);
}

const [, majorText, minorText, patchText] = match;
const major = Number(majorText);
const minor = Number(minorText);
const patch = Number(patchText);
const [minimumMinor, minimumPatch] = minimumByMajor.get(major);

if (minor < minimumMinor || (minor === minimumMinor && patch < minimumPatch)) {
  console.error(
    `Node ${version} is below its security floor; use stable Node 22.23.4+, 24.22.1+, or 26.12.1+.`,
  );
  process.exit(1);
}

console.log(`Node ${version} satisfies the supported security floor.`);
