// Generates the Material 3 colour tokens for the dashboard (light + dark) from one seed colour.
// Used by build.mjs. Reference implementation: @material/material-color-utilities.
import { argbFromHex, hexFromArgb, Hct, SchemeTonalSpot, MaterialDynamicColors, DynamicColor, customColor } from '@material/material-color-utilities';

export const SEED = '#7de5c0';
// Service and chart accents. Each name becomes --md-custom-<name>, --md-custom-on-<name>,
// --md-custom-<name>-container and --md-custom-on-<name>-container, harmonised toward the seed.
export const CUSTOM = {
  teal: '#7de5c0', green: '#a8d798', cyan: '#8acfe2', blue: '#7dacf5', indigo: '#9aadee',
  purple: '#c3a5f4', pink: '#f49b99', orange: '#ef9c65', amber: '#f4cf7d',
};
const ROLES = Object.getOwnPropertyNames(MaterialDynamicColors)
  .filter(k => MaterialDynamicColors[k] instanceof DynamicColor && !k.endsWith('PaletteKeyColor'));
const kebab = s => s.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

function block(isDark) {
  const scheme = new SchemeTonalSpot(Hct.fromInt(argbFromHex(SEED)), isDark, 0);
  const lines = ROLES.map(k => `--md-sys-color-${kebab(k)}:${hexFromArgb(MaterialDynamicColors[k].getArgb(scheme))}`);
  for (const [name, hex] of Object.entries(CUSTOM)) {
    const c = customColor(argbFromHex(SEED), { name, value: argbFromHex(hex), blend: true })[isDark ? 'dark' : 'light'];
    lines.push(`--md-custom-${name}:${hexFromArgb(c.color)}`, `--md-custom-on-${name}:${hexFromArgb(c.onColor)}`,
      `--md-custom-${name}-container:${hexFromArgb(c.colorContainer)}`, `--md-custom-on-${name}-container:${hexFromArgb(c.onColorContainer)}`);
  }
  return lines.join(';');
}

export function m3TokensCss() {
  return `/* GENERATED: m3-tokens (m3-tokens.mjs, seed ${SEED}) */:root{color-scheme:dark;${block(true)}}:root[data-theme=light]{color-scheme:light;${block(false)}}`;
}
