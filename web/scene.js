import * as THREE from "three";

// Bold, playful ambient backdrop: a drift of rounded candy solids that slowly
// tumble, with mild mouse parallax. app.js calls window.cockpit.setMood(t) with
// t in [-1,1] (net bias) to warm the palette toward up-green or down-magenta.

const canvas = document.getElementById("bg");
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setClearColor(0x000000, 0);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
camera.position.z = 16;

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.PointLight(0x8a5bff, 900, 120); key.position.set(10, 14, 18); scene.add(key);
const rim = new THREE.PointLight(0x22e3ff, 700, 120); rim.position.set(-16, -8, 10); scene.add(rim);

const PALETTE = [0xff3d9a, 0x22e3ff, 0xb4ff3d, 0x8a5bff, 0xffd23d];
const geos = [
  new THREE.IcosahedronGeometry(1, 0),
  new THREE.TorusGeometry(0.8, 0.33, 16, 40),
  new THREE.DodecahedronGeometry(1, 0),
  new THREE.CapsuleGeometry(0.5, 0.9, 6, 14),
  new THREE.OctahedronGeometry(1, 0),
];

const shapes = [];
const COUNT = window.innerWidth < 700 ? 11 : 18;
for (let i = 0; i < COUNT; i++) {
  const color = PALETTE[i % PALETTE.length];
  const mat = new THREE.MeshStandardMaterial({
    color, roughness: 0.35, metalness: 0.15,
    emissive: color, emissiveIntensity: 0.12, flatShading: true,
  });
  const mesh = new THREE.Mesh(geos[i % geos.length], mat);
  const s = 0.6 + Math.random() * 1.7;
  mesh.scale.setScalar(s);
  mesh.position.set((Math.random() - 0.5) * 34, (Math.random() - 0.5) * 20, (Math.random() - 0.5) * 14 - 4);
  mesh.rotation.set(Math.random() * 6, Math.random() * 6, 0);
  mesh.userData = {
    spin: new THREE.Vector3((Math.random() - 0.5) * 0.4, (Math.random() - 0.5) * 0.4, 0),
    floatPhase: Math.random() * 6.28, floatAmp: 0.4 + Math.random() * 0.7, baseY: mesh.position.y,
    baseColor: new THREE.Color(color),
  };
  scene.add(mesh);
  shapes.push(mesh);
}

let targetMood = 0, mood = 0;
const upC = new THREE.Color(0x28e0a0), downC = new THREE.Color(0xff3d9a);
window.cockpit = {
  setMood(t) { targetMood = Math.max(-1, Math.min(1, t)); },
};

const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
addEventListener("pointermove", (e) => {
  mouse.tx = (e.clientX / innerWidth - 0.5) * 2;
  mouse.ty = (e.clientY / innerHeight - 0.5) * 2;
});

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener("resize", resize); resize();

const clock = new THREE.Clock();
function tick() {
  const t = clock.getElapsedTime();
  const dt = Math.min(clock.getDelta ? 0.016 : 0.016, 0.033);
  mood += (targetMood - mood) * 0.03;
  mouse.x += (mouse.tx - mouse.x) * 0.04; mouse.y += (mouse.ty - mouse.y) * 0.04;
  camera.position.x = mouse.x * 2.2; camera.position.y = -mouse.y * 1.6; camera.lookAt(0, 0, 0);

  const tint = mood >= 0 ? upC : downC;
  const tintAmt = Math.abs(mood) * 0.5;
  for (const m of shapes) {
    const u = m.userData;
    if (!reduce) {
      m.rotation.x += u.spin.x * 0.01; m.rotation.y += u.spin.y * 0.01;
      m.position.y = u.baseY + Math.sin(t * 0.5 + u.floatPhase) * u.floatAmp;
    }
    m.material.color.copy(u.baseColor).lerp(tint, tintAmt);
    m.material.emissive.copy(u.baseColor).lerp(tint, tintAmt);
  }
  key.color.lerp(mood >= 0 ? upC : new THREE.Color(0x8a5bff), 0.02);
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}
tick();
