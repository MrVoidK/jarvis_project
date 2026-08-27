import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { JarvisState } from '../types';
import './HologramOrb.css';

interface StateProfile {
  color: THREE.Color;
  rotationSpeed: number;
  jitter: number;
  glow: number;
  pulseSpeed: number;
  pulseAmplitude: number;
}

// Durum -> gorsel hedef profil. Amber ailesinin disina cikmadan (marka
// rengi tek: #FFBF00) sadece YOGUNLUK/HIZ/ton kaydirilarak durumlar
// ayristiriliyor - bkz. CLAUDE.md "Kehribar/Amber" estetik karari.
//
// DEGERLER BILINCLI OLARAK YAVAS (kullanici geri bildirimi: ilk surum
// "mekanik"/cok hizli hissettiriyordu) - pulseSpeed bir "nefes alma"
// ritmi olacak sekilde dusuk tutuluyor (en hizli durum olan "speaking"
// bile ~0.5 Hz, bir insan nefesinden hizli degil), animate()'teki sabit
// bir "breath" katmaniyla birlesip organik bir his veriyor.
const PROFILES: Record<JarvisState, StateProfile> = {
  idle: {
    color: new THREE.Color('#c98f1c'),
    rotationSpeed: 0.045,
    jitter: 0.012,
    glow: 0.5,
    pulseSpeed: 0.35,
    pulseAmplitude: 0.025,
  },
  listening: {
    color: new THREE.Color('#ffd866'),
    rotationSpeed: 0.1,
    jitter: 0.03,
    glow: 0.8,
    pulseSpeed: 0.85,
    pulseAmplitude: 0.04,
  },
  processing: {
    color: new THREE.Color('#ff8c1a'),
    rotationSpeed: 0.26,
    jitter: 0.06,
    glow: 1.0,
    pulseSpeed: 1.7,
    pulseAmplitude: 0.045,
  },
  speaking: {
    color: new THREE.Color('#fff2b0'),
    rotationSpeed: 0.16,
    jitter: 0.045,
    glow: 1.15,
    pulseSpeed: 2.6,
    pulseAmplitude: 0.07,
  },
};

function makeGlowTexture(): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.35, 'rgba(255,210,120,0.7)');
  gradient.addColorStop(1, 'rgba(255,191,0,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

// Basit deterministik gurultu (Perlin/Simplex'e gerek yok - kucuk parcacik
// sayisinda goz, kaba bir sinus-toplami "titresim"i yeterince organik algiliyor,
// harici bir bagimliligin (bundle boyutu) gerekcesi yok). Ic frekans
// carpanlari BILINCLI OLARAK dusuk (0.4-0.9 araligi, eskiden 0.7-2.1 idi) -
// daha yavas/dalgali bir "shimmer", titreme degil.
function noise3(x: number, y: number, z: number, t: number): number {
  return (
    Math.sin(x * 3.1 + t) * Math.cos(y * 2.7 - t * 0.4) * Math.sin(z * 3.3 + t * 0.6) * 0.5 +
    Math.sin((x + y + z) * 1.7 + t * 0.9) * 0.5
  );
}

interface HologramOrbProps {
  state: JarvisState;
}

export function HologramOrb({ state }: HologramOrbProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<JarvisState>(state);
  stateRef.current = state;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    // KAMERA NOTU (kullanici bulgusu: buyurken "gorunmez bir kare cerceve"de
    // kesiliyordu): eski kurulum (fov 45, z=4.4) icin gorunur frustum
    // yari-yuksekligi ~1.82 dunya-birimiydi, ama halkalarin yaricapi (2.1'e
    // kadar) pulse ile bunu ASIYORDU - kesme, canvas'in KENDI piksel
        // sinirindan degil, 3B kameranin gorus alanindan kaynaklaniyordu.
    // z=6.2/fov=42 ile yari-yukseklik ~2.38'e cikarildi VE asagida tum
    // geometriler (parcacik kuresi/cekirdek/halkalar) kucultuldu - en
    // buyuk olasi genisleme (halka + max pulse) artik bu sinirin ~%70'inde
    // kaliyor, guvenli pay birakiyor.
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 0.1, 6.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    // ---- parcacik kure (enerji globu) ----
    const PARTICLE_COUNT = 2600;
    const basePositions = new Float32Array(PARTICLE_COUNT * 3);
    const positions = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const phi = Math.acos(2 * Math.random() - 1);
      const theta = Math.random() * Math.PI * 2;
      const r = 1.05 + Math.random() * 0.06;
      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.sin(phi) * Math.sin(theta);
      const z = r * Math.cos(phi);
      basePositions[i * 3] = x;
      basePositions[i * 3 + 1] = y;
      basePositions[i * 3 + 2] = z;
      positions[i * 3] = x;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = z;
    }
    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const glowTexture = makeGlowTexture();
    const particleMaterial = new THREE.PointsMaterial({
      size: 0.05,
      map: glowTexture,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      color: PROFILES.idle.color.clone(),
    });
    const particles = new THREE.Points(particleGeometry, particleMaterial);
    scene.add(particles);

    // ---- ic wireframe cekirdek ----
    const coreGeometry = new THREE.IcosahedronGeometry(0.68, 1);
    const coreEdges = new THREE.EdgesGeometry(coreGeometry);
    const coreMaterial = new THREE.LineBasicMaterial({
      color: PROFILES.idle.color.clone(),
      transparent: true,
      opacity: 0.5,
    });
    const core = new THREE.LineSegments(coreEdges, coreMaterial);
    scene.add(core);

    // ---- donen halkalar (arc-reactor) ----
    // Yaricaplar KUCULTULDU (eskiden 1.6-2.1) - bkz. yukaridaki kamera notu.
    function makeRing(radius: number, segments: number): THREE.LineLoop {
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i <= segments; i++) {
        const a = (i / segments) * Math.PI * 2;
        pts.push(new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0));
      }
      const geometry = new THREE.BufferGeometry().setFromPoints(pts);
      const material = new THREE.LineBasicMaterial({
        color: PROFILES.idle.color.clone(),
        transparent: true,
        opacity: 0.6,
      });
      return new THREE.LineLoop(geometry, material);
    }

    const ringA = makeRing(1.45, 96);
    ringA.rotation.x = Math.PI / 2.3;
    const ringB = makeRing(1.65, 96);
    ringB.rotation.x = Math.PI / 2;
    ringB.rotation.y = Math.PI / 5;
    const ringC = makeRing(1.25, 72);
    ringC.rotation.x = Math.PI / 1.8;
    ringC.rotation.z = Math.PI / 6;
    const rings = [ringA, ringB, ringC];
    rings.forEach((ring) => scene.add(ring));

    // ---- merkez parlama (sprite, ek-parlaklik hissi icin) ----
    const glowSprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTexture,
        color: PROFILES.idle.color.clone(),
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    glowSprite.scale.set(1.8, 1.8, 1);
    scene.add(glowSprite);

    // ---- disaridaki cok yumusak "atmosfer" halesi (gradyan, retro-hologram
    // hissi icin) - buyuk ama cok dusuk opaklik, hicbir zaman sert kenar
    // gostermiyor (dokusu zaten kenarda tamamen saydam) bu yuzden frustum
    // sinirina yakin olmasi sorun degil.
    const atmosphere = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTexture,
        color: PROFILES.idle.color.clone(),
        transparent: true,
        opacity: 0.12,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    atmosphere.scale.set(4.2, 4.2, 1);
    scene.add(atmosphere);

    function resize() {
      const width = mount!.clientWidth;
      const height = mount!.clientHeight;
      if (width === 0 || height === 0) return;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    }
    resize();
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(mount);

    // ---- lerp edilen "canli" gorsel parametreler ----
    const live = {
      color: PROFILES.idle.color.clone(),
      rotationSpeed: PROFILES.idle.rotationSpeed,
      jitter: PROFILES.idle.jitter,
      glow: PROFILES.idle.glow,
      pulseSpeed: PROFILES.idle.pulseSpeed,
      pulseAmplitude: PROFILES.idle.pulseAmplitude,
    };

    let frameId = 0;
    const clock = new THREE.Clock();

    function animate() {
      frameId = requestAnimationFrame(animate);
      const dt = Math.min(clock.getDelta(), 0.05);
      const t = clock.elapsedTime;
      const target = PROFILES[stateRef.current];

      // Durum gecisleri ~1-2s'de yumusakca (lerp) tamamlaniyor - ani bir
      // "atlama" yerine bir durumdan digerine akiyor gibi hissettiriyor.
      const lerpFactor = 1 - Math.pow(0.001, dt);
      live.color.lerp(target.color, lerpFactor);
      live.rotationSpeed += (target.rotationSpeed - live.rotationSpeed) * lerpFactor;
      live.jitter += (target.jitter - live.jitter) * lerpFactor;
      live.glow += (target.glow - live.glow) * lerpFactor;
      live.pulseSpeed += (target.pulseSpeed - live.pulseSpeed) * lerpFactor;
      live.pulseAmplitude += (target.pulseAmplitude - live.pulseAmplitude) * lerpFactor;

      // Her zaman aktif, cok yavas bir "nefes" katmani (durumdan bagimsiz,
      // sabit genlik) - durum-ozgu pulse'un ustune binerek hicbir zaman
      // tamamen duragan/mekanik gorunmemesini sagliyor.
      const breath = Math.sin(t * 0.28) * 0.018;
      const pulse = 1 + breath + Math.sin(t * live.pulseSpeed) * live.pulseAmplitude;

      particles.rotation.y += dt * live.rotationSpeed;
      particles.rotation.x = Math.sin(t * 0.12) * 0.1;
      const posAttr = particleGeometry.attributes.position as THREE.BufferAttribute;
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const bx = basePositions[i * 3];
        const by = basePositions[i * 3 + 1];
        const bz = basePositions[i * 3 + 2];
        const n = noise3(bx, by, bz, t * 0.8) * live.jitter;
        posAttr.array[i * 3] = bx * (1 + n) * pulse;
        posAttr.array[i * 3 + 1] = by * (1 + n) * pulse;
        posAttr.array[i * 3 + 2] = bz * (1 + n) * pulse;
      }
      posAttr.needsUpdate = true;
      particleMaterial.color.copy(live.color);
      particleMaterial.opacity = Math.min(1, 0.55 + live.glow * 0.3);

      core.rotation.y -= dt * live.rotationSpeed * 0.6;
      core.rotation.x += dt * live.rotationSpeed * 0.35;
      core.scale.setScalar(pulse * 0.97);
      coreMaterial.color.copy(live.color);

      // Halkalar pulse'a SADECE KISMEN tepki veriyor (0.4 faktoru) - tam
      // pulse ile birlikte buyuseler frustum sinirina cok yaklasirlardi
      // (bkz. yukaridaki kamera notu); ayrica gorsel olarak da "ic kure
      // nefes alirken disaridaki halkalar sabit bir yorunge cizer" hissi
      // gercek bir arc-reactor'a daha yakin.
      const ringPulse = 1 + (pulse - 1) * 0.4;
      rings.forEach((ring, i) => {
        const dir = i % 2 === 0 ? 1 : -1;
        ring.rotation.z += dt * live.rotationSpeed * (0.4 + i * 0.18) * dir;
        (ring.material as THREE.LineBasicMaterial).color.copy(live.color);
        (ring.material as THREE.LineBasicMaterial).opacity = 0.35 + live.glow * 0.3;
        ring.scale.setScalar(ringPulse);
      });

      glowSprite.material.color.copy(live.color);
      const glowScale = (1.3 + live.glow * 0.6) * pulse;
      glowSprite.scale.set(glowScale, glowScale, 1);
      glowSprite.material.opacity = 0.35 + live.glow * 0.35;

      atmosphere.material.color.copy(live.color);
      atmosphere.material.opacity = 0.08 + live.glow * 0.07;

      renderer.render(scene, camera);
    }
    animate();

    return () => {
      cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      renderer.dispose();
      particleGeometry.dispose();
      particleMaterial.dispose();
      coreGeometry.dispose();
      coreEdges.dispose();
      coreMaterial.dispose();
      glowTexture.dispose();
      rings.forEach((ring) => {
        ring.geometry.dispose();
        (ring.material as THREE.LineBasicMaterial).dispose();
      });
      if (renderer.domElement.parentElement === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <div className="jv-orb-wrap">
      <div ref={mountRef} className="jv-orb-canvas" />
      <div className={`jv-orb-state-label jv-orb-state-${state}`}>{state.toUpperCase()}</div>
    </div>
  );
}
