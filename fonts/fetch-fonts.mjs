import { readFile, writeFile } from 'node:fs/promises';
import { existsSync, readdirSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import crypto from 'node:crypto';
import { ICONS } from '../m3-icons.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';

const sortedIcons = [...new Set(ICONS)].sort().join(',');
const iconsHash = crypto.createHash('sha256').update(sortedIcons).digest('hex');

const textUrl = 'https://fonts.googleapis.com/css2?family=Google+Sans+Flex:opsz,wght@6..144,1..1000&family=Google+Sans+Code:wght@300..800&family=Roboto+Flex:opsz,wght@8..144,100..1000&display=swap';
const symbolsUrl = `https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400,0..1,0&icon_names=${sortedIcons}&display=block`;

console.log('Fetching Google Fonts CSS...');
const [textRes, symbolsRes] = await Promise.all([
  fetch(textUrl, { headers: { 'User-Agent': UA } }),
  fetch(symbolsUrl, { headers: { 'User-Agent': UA } }),
]);

if (!textRes.ok) throw new Error(`Failed to fetch text fonts CSS: ${textRes.status}`);
if (!symbolsRes.ok) throw new Error(`Failed to fetch symbols CSS: ${symbolsRes.status}`);

const textCss = await textRes.text();
const symbolsCss = await symbolsRes.text();

// Remove old woff2 files in fonts/
for (const file of readdirSync(__dirname)) {
  if (file.endsWith('.woff2')) {
    unlinkSync(join(__dirname, file));
  }
}

const fontFiles = {};
const cssRules = [];

// Parse text CSS: find latin blocks
const blocks = textCss.split(/(?=\/\*\s*[a-z0-9-]+\s*\*\/)/);

for (const block of blocks) {
  if (!block.trim().startsWith('/* latin */')) continue;
  const famMatch = block.match(/font-family:\s*['"]?([^'";]+)['"]?;/);
  const urlMatch = block.match(/url\((https:[^)]+)\)/);
  if (!famMatch || !urlMatch) continue;

  const familyName = famMatch[1].trim();
  const fontUrl = urlMatch[1].trim();
  const slug = familyName.toLowerCase().replace(/[^a-z0-9]+/g, '-');

  console.log(`Downloading ${familyName} (latin)...`);
  const fontRes = await fetch(fontUrl, { headers: { 'User-Agent': UA } });
  if (!fontRes.ok) throw new Error(`Failed to download font ${fontUrl}: ${fontRes.status}`);
  const fontBuf = Buffer.from(await fontRes.arrayBuffer());
  const hash8 = crypto.createHash('sha256').update(fontBuf).digest('hex').slice(0, 8);
  const fileName = `${slug}-latin-${hash8}.woff2`;

  await writeFile(join(__dirname, fileName), fontBuf);
  fontFiles[slug] = fileName;

  // Rewrite block with local URL and font-display: swap
  let rewritten = block.replace(/\/\*\s*latin\s*\*\/\s*/, '').trim();
  rewritten = rewritten.replace(urlMatch[0], `url(/static/fonts/${fileName})`);
  rewritten = rewritten.replace(/font-display:\s*[^;]+;/, 'font-display: swap;');
  cssRules.push(rewritten);
}

// Parse symbols CSS
const symUrlMatch = symbolsCss.match(/url\((https:[^)]+)\)/);
if (!symUrlMatch) throw new Error('No symbols font URL found in CSS');

console.log('Downloading Material Symbols Rounded...');
const symRes = await fetch(symUrlMatch[1].trim(), { headers: { 'User-Agent': UA } });
if (!symRes.ok) throw new Error(`Failed to download symbols font: ${symRes.status}`);
const symBuf = Buffer.from(await symRes.arrayBuffer());
const symHash8 = crypto.createHash('sha256').update(symBuf).digest('hex').slice(0, 8);
const symFileName = `material-symbols-rounded-${symHash8}.woff2`;

await writeFile(join(__dirname, symFileName), symBuf);
fontFiles['material-symbols-rounded'] = symFileName;

// Extract @font-face for symbols
const symFaceMatch = symbolsCss.match(/@font-face\s*\{[^}]+\}/);
if (!symFaceMatch) throw new Error('No @font-face found in symbols CSS');

let symFace = symFaceMatch[0];
symFace = symFace.replace(symUrlMatch[0], `url(/static/fonts/${symFileName})`);
symFace = symFace.replace(/font-display:\s*[^;]+;/, 'font-display: block;');
cssRules.push(symFace);

const fontsCss = cssRules.join('\n\n') + '\n';
await writeFile(join(__dirname, 'fonts.css'), fontsCss);

const manifest = {
  icons_hash: iconsHash,
  fonts: fontFiles,
};
await writeFile(join(__dirname, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log('Done! Downloaded fonts and generated fonts.css + manifest.json.');
