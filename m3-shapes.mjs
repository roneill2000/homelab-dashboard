// M3 Expressive shapes as CSS clip-path polygons, plus the Expressive loading indicator that morphs between them.
// Material's own shapes (androidx.graphics.shapes MaterialShapes) are rounded polygons; these are close polar
// approximations, r(t) = 1 + a*cos(n*t), sampled at the same number of points so CSS can morph one into another
// (clip-path transitions interpolate polygons point by point when the counts match).
const N = 96;
// [lobes, depth, rotation in degrees]. Names follow MaterialShapes; the loading indicator cycles the first seven
// (Material's sequence: soft burst, 9-sided cookie, pentagon, pill, sunny, 4-sided cookie, oval).
export const SHAPES = {
  'soft-burst': [10, 0.11, 0], 'cookie-9': [9, 0.075, 0], pentagon: [5, 0.07, -90], pill: [2, 0.2, 0],
  sunny: [8, 0.045, 0], 'cookie-4': [4, 0.1, 45], oval: [2, 0.09, 90],
  'cookie-12': [12, 0.05, 0], 'clover-4': [4, 0.2, 45], circle: [1, 0, 0],
};
function polygon([n, a, rot]) {
  const pts = [];
  for (let i = 0; i < N; i++) {
    const t = (i / N) * 2 * Math.PI, r = (1 + a * Math.cos(n * (t - rot * Math.PI / 180))) / (1 + a);
    pts.push(`${(50 + 50 * r * Math.cos(t)).toFixed(2)}% ${(50 + 50 * r * Math.sin(t)).toFixed(2)}%`);
  }
  return `polygon(${pts.join(',')})`;
}
const LOADING = ['soft-burst', 'cookie-9', 'pentagon', 'pill', 'sunny', 'cookie-4', 'oval'];
export function m3ShapesCss() {
  const vars = Object.entries(SHAPES).map(([k, v]) => `--m3-shape-${k}:${polygon(v)}`).join(';');
  // Each shape holds briefly, then morphs on the Expressive fast spatial spring feel; the whole indicator also turns.
  const step = 100 / LOADING.length;
  const frames = LOADING.map((k, i) => `${(i * step).toFixed(2)}%,${(i * step + step * 0.45).toFixed(2)}%{clip-path:${polygon(SHAPES[k])}}`).join('')
    + `100%{clip-path:${polygon(SHAPES[LOADING[0]])}}`;
  return `/* GENERATED: m3-shapes.mjs */:root{${vars}}@keyframes m3-loading-morph{${frames}}@keyframes m3-loading-turn{to{transform:rotate(360deg)}}`;
}
