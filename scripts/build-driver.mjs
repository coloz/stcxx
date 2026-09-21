import {spawnSync} from 'node:child_process';
import {copyFileSync, readdirSync, mkdirSync, writeFileSync} from 'node:fs';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(repo, 'compiler');
function run(command, args, capture = false) {
  const r = spawnSync(command, args, {cwd: root, encoding: 'utf8', stdio: capture ? 'pipe' : 'inherit', maxBuffer: 16*1024*1024, windowsHide: true});
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`${command} failed (${r.status}): ${r.stderr ?? ''}`);
  return r.stdout;
}
run('cargo', ['build', '--release', '--locked', '--offline']);
const host = run('rustc', ['-vV'], true).match(/^host: (.+)$/m)?.[1];
if (!host) throw new Error('Cannot determine Rust host');
const metadata = JSON.parse(run('cargo', ['metadata', '--format-version', '1', '--locked', '--offline', '--filter-platform', host], true));
const included = new Set(metadata.resolve.nodes.map(n => n.id));
const notices = [];
for (const pkg of metadata.packages.filter(p => included.has(p.id)).sort((a,b) => a.id.localeCompare(b.id))) {
  const source = dirname(pkg.manifest_path);
  const files = readdirSync(source, {withFileTypes: true}).filter(f => f.isFile() && /^(LICENSE|COPYING|NOTICE)([-.]|$)/i.test(f.name));
  if (!files.length) throw new Error(`Missing dependency license: ${pkg.id}`);
  const destination = resolve(root, 'LICENSES', `${pkg.name}-${pkg.version}`);
  mkdirSync(destination, {recursive: true});
  for (const file of files) copyFileSync(resolve(source, file.name), resolve(destination, file.name));
  notices.push({name: pkg.name, version: pkg.version, license: pkg.license, repository: pkg.repository, files: files.map(f => f.name)});
}
writeFileSync(resolve(root, 'LICENSES', `dependencies-${host}.json`), JSON.stringify(notices, null, 2)+'\n');
console.log(resolve(root, 'target/release', process.platform === 'win32' ? 'stcxx.exe' : 'stcxx'));
