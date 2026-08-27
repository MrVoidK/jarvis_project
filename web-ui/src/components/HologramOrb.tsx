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
// ritmi olacak sekilde dusuk tutuluyor, animate()'teki sabit bir "breath"
// katmaniyla birlesip organik bir his veriyor. "idle" ve "listening"in
// KENDINE OZGU davranisi asagida ayrica var (uyanma flası + sonar-ping
// halkalari, bkz. animate() icindeki ilgili bolumler) - "processing"/
// "speaking" kullanici talebiyle DEGISTIRILMEDI.
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

// Uyanma flası (idle -> listening gecisi, bkz. animate()'teki wakeBurst
// mantigi) - kisa, belirgin bir "canlanma" anı.
const WAKE_BURST_DURATION_S = 0.6;
const WAKE_BURST_AMPLITUDE = 0.22;

// "Sonar ping" halkalari (SADECE listening'de gorunur, bkz. listeningWeight) -
// dinleme durumunun idle'dan BELIRGIN sekilde ayrismasi icin (kullanici
// talebi: "dinleme modunda olduğunda belirgin olsun").
const PING_RING_COUNT = 2;
const PING_CYCLE_S = 1.7;
const PING_MIN_RADIUS = 1.05;
const PING_MAX_RADIUS = 2.0;

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

function makeUnitCircle(segments: number): THREE.BufferGeometry {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a), Math.sin(a), 0));
  }
  return new THREE.BufferGeometry().setFromPoints(pts);
}

// Parcacik kuresinin titresimi ARTIK GPU'da (vertex shader) hesaplaniyor -
// eskiden HER karede 2600 parcacik icin CPU'da (JS dongusu) trig cagrilari
// yapiliyordu; kullanicinin makinesinde Ears/Brain/Mouth (faster-whisper/
// Ollama/XTTS) ZATEN CPU'yu doyuma yakin kullanirken bu ek is tarayicinin
// ana thread'ini tikatip GORULEBILIR TAKILMAYA (jank) yol aciyordu (kullanici
// bulgusu: "hareketi hala bozuk, takılma olabiliyor"). GPU'da bu maliyet
// PARCACIK SAYISINDAN BAGIMSIZ, sabit (birkac uniform yazmak) - noise3()'un
// AYNI matematigi burada GLSL'e tasindi.
const PARTICLE_VERTEX_SHADER = `
  uniform float uTime;
  uniform float uJitter;
  uniform float uPulse;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uViewportHeight;

  float noise3(vec3 p, float t) {
    return sin(p.x * 3.1 + t) * cos(p.y * 2.7 - t * 0.4) * sin(p.z * 3.3 + t * 0.6) * 0.5
         + sin((p.x + p.y + p.z) * 1.7 + t * 0.9) * 0.5;
  }

  void main() {
    float n = noise3(position, uTime) * uJitter;
    vec3 displaced = position * (1.0 + n) * uPulse;
    vec4 mvPosition = modelViewMatrix * vec4(displaced, 1.0);
    gl_Position = projectionMatrix * mvPosition;
    gl_PointSize = uSize * uPixelRatio * (uViewportHeight / -mvPosition.z);
  }
`;

const PARTICLE_FRAGMENT_SHADER = `
  uniform sampler2D uMap;
  uniform vec3 uColor;
  uniform float uOpacity;

  void main() {
    vec4 tex = texture2D(uMap, gl_PointCoord);
    gl_FragColor = vec4(uColor, 1.0) * tex * uOpacity;
  }
`;

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
    // kesiliyordu): fov 42 / z=6.2 ile gorunur frustum yari-yuksekligi ~2.38
    // dunya-birimi - asagidaki tum geometriler (parcacik kuresi/cekirdek/
    // halkalar, ping halkalari dahil) bu sinirin guvenli icinde kaliyor.
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 0.1, 6.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    const pixelRatio = Math.min(window.devicePixelRatio, 2);
    renderer.setPixelRatio(pixelRatio);
    mount.appendChild(renderer.domElement);

    // ---- parcacik kure (enerji globu) - GPU shader ile ----
    const PARTICLE_COUNT = 2600;
    const basePositions = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const phi = Math.acos(2 * Math.random() - 1);
      const theta = Math.random() * Math.PI * 2;
      const r = 1.05 + Math.random() * 0.06;
      basePositions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      basePositions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      basePositions[i * 3 + 2] = r * Math.cos(phi);
    }
    const particleGeometry = new THREE.BufferGeometry();
    // `position` DEGISMEZ (statik) - eskiden her karede posAttr.needsUpdate
    // ile CPU'dan yeniden yazilirdi, artik SADECE bir kez yukleniyor;
    // gorsel titresim tamamen vertex shader'in ELINDE (bkz. yukaridaki not).
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(basePositions, 3));
    const glowTexture = makeGlowTexture();
    const particleUniforms = {
      uTime: { value: 0 },
      uJitter: { value: PROFILES.idle.jitter },
      uPulse: { value: 1 },
      uSize: { value: 0.034 },
      uPixelRatio: { value: pixelRatio },
      uViewportHeight: { value: 640 },
      uMap: { value: glowTexture },
      uColor: { value: PROFILES.idle.color.clone() },
      uOpacity: { value: 0.7 },
    };
    const particleMaterial = new THREE.ShaderMaterial({
      uniforms: particleUniforms,
      vertexShader: PARTICLE_VERTEX_SHADER,
      fragmentShader: PARTICLE_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
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
    function makeRing(radius: number, segments: number): THREE.LineLoop {
      const geometry = makeUnitCircle(segments);
      geometry.scale(radius, radius, 1);
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

    // ---- "sonar ping" halkalari - SADECE listening'de belirgin (kullanici
    // talebi: dinleme durumu acikca ayirt edilsin). Kameraya DONUK (rotasyon
    // yok, XY duzleminde) - digerlerinin aksine egik degil, "disariya yayilan
    // dalga" gibi net okunuyor. Taban birim cember + her karede SADECE
    // scale/opacity guncelleniyor (geometri yeniden hesaplanmiyor, ucuz).
    const pingGeometry = makeUnitCircle(80);
    const pingRings = Array.from({ length: PING_RING_COUNT }, () => {
      const material = new THREE.LineBasicMaterial({
        color: PROFILES.listening.color.clone(),
        transparent: true,
        opacity: 0,
      });
      const ring = new THREE.LineLoop(pingGeometry, material);
      scene.add(ring);
      return ring;
    });

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
      particleUniforms.uViewportHeight.value = height;
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
      listeningWeight: 0,
    };
    let prevState: JarvisState = stateRef.current;
    let wakeBurstStart = -Infinity;

    let frameId = 0;
    const clock = new THREE.Clock();

    function animate() {
      frameId = requestAnimationFrame(animate);
      const dt = Math.min(clock.getDelta(), 0.05);
      const t = clock.elapsedTime;
      const target = PROFILES[stateRef.current];

      // Uyanma flası: idle (veya baska bir durum) -> listening GECISININ TAM
      // ANINDA tetiklenir - "wakeword geldigi an bi hareketlensin" talebi.
      if (prevState !== 'listening' && stateRef.current === 'listening') {
        wakeBurstStart = t;
      }
      prevState = stateRef.current;

      // Durum gecisleri ~1-2s'de yumusakca (lerp) tamamlaniyor - ani bir
      // "atlama" yerine bir durumdan digerine akiyor gibi hissettiriyor.
      const lerpFactor = 1 - Math.pow(0.001, dt);
      live.color.lerp(target.color, lerpFactor);
      live.rotationSpeed += (target.rotationSpeed - live.rotationSpeed) * lerpFactor;
      live.jitter += (target.jitter - live.jitter) * lerpFactor;
      live.glow += (target.glow - live.glow) * lerpFactor;
      live.pulseSpeed += (target.pulseSpeed - live.pulseSpeed) * lerpFactor;
      live.pulseAmplitude += (target.pulseAmplitude - live.pulseAmplitude) * lerpFactor;
      const listeningTarget = stateRef.current === 'listening' ? 1 : 0;
      live.listeningWeight += (listeningTarget - live.listeningWeight) * lerpFactor;

      // Her zaman aktif, cok yavas bir "nefes" katmani (durumdan bagimsiz,
      // sabit genlik) - durum-ozgu pulse'un ustune binerek hicbir zaman
      // tamamen duragan/mekanik gorunmemesini sagliyor.
      const breath = Math.sin(t * 0.28) * 0.018;

      // Uyanma flasinin ani-sonrasi sonup giden katkisi (ease-out).
      const burstAge = t - wakeBurstStart;
      const burstEnvelope =
        burstAge >= 0 && burstAge < WAKE_BURST_DURATION_S
          ? Math.pow(1 - burstAge / WAKE_BURST_DURATION_S, 2)
          : 0;

      const pulse =
        1 + breath + Math.sin(t * live.pulseSpeed) * live.pulseAmplitude + burstEnvelope * WAKE_BURST_AMPLITUDE;

      particles.rotation.y += dt * live.rotationSpeed;
      particles.rotation.x = Math.sin(t * 0.12) * 0.1;
      particleUniforms.uTime.value = t * 0.8;
      particleUniforms.uJitter.value = live.jitter;
      particleUniforms.uPulse.value = pulse;
      (particleUniforms.uColor.value as THREE.Color).copy(live.color);
      particleUniforms.uOpacity.value = Math.min(1, 0.55 + live.glow * 0.3 + burstEnvelope * 0.3);

      core.rotation.y -= dt * live.rotationSpeed * 0.6;
      core.rotation.x += dt * live.rotationSpeed * 0.35;
      core.scale.setScalar(pulse * 0.97);
      coreMaterial.color.copy(live.color);

      // Halkalar pulse'a SADECE KISMEN tepki veriyor (0.4 faktoru) - tam
      // pulse ile birlikte buyuseler frustum sinirina cok yaklasirlardi;
      // ayrica gorsel olarak da "ic kure nefes alirken disaridaki halkalar
      // sabit bir yorunge cizer" hissi gercek bir arc-reactor'a daha yakin.
      const ringPulse = 1 + (pulse - 1) * 0.4;
      rings.forEach((ring, i) => {
        const dir = i % 2 === 0 ? 1 : -1;
        ring.rotation.z += dt * live.rotationSpeed * (0.4 + i * 0.18) * dir;
        (ring.material as THREE.LineBasicMaterial).color.copy(live.color);
        (ring.material as THREE.LineBasicMaterial).opacity =
          0.35 + live.glow * 0.3 + burstEnvelope * 0.4;
        ring.scale.setScalar(ringPulse);
      });

      // Sonar-ping halkalari: SADECE listeningWeight > 0 iken gorunur olur -
      // digerlerinde (idle/processing/speaking) tamamen saydam kalirlar
      // (kullanici talebi: "diğerleri aynı kalabilir").
      if (live.listeningWeight > 0.01) {
        pingRings.forEach((ring, i) => {
          const phase = ((t / PING_CYCLE_S + i / PING_RING_COUNT) % 1) + 1e-4;
          const radius = PING_MIN_RADIUS + phase * (PING_MAX_RADIUS - PING_MIN_RADIUS);
          const fade = (1 - phase) * live.listeningWeight;
          ring.scale.setScalar(radius);
          const material = ring.material as THREE.LineBasicMaterial;
          material.opacity = fade * 0.5;
          material.color.copy(live.color);
        });
      } else {
        pingRings.forEach((ring) => {
          (ring.material as THREE.LineBasicMaterial).opacity = 0;
        });
      }

      glowSprite.material.color.copy(live.color);
      const glowScale = (1.3 + live.glow * 0.6 + burstEnvelope * 0.5) * pulse;
      glowSprite.scale.set(glowScale, glowScale, 1);
      glowSprite.material.opacity = 0.35 + live.glow * 0.35 + burstEnvelope * 0.3;

      atmosphere.material.color.copy(live.color);
      atmosphere.material.opacity = 0.08 + live.glow * 0.07 + burstEnvelope * 0.15;

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
      pingGeometry.dispose();
      pingRings.forEach((ring) => (ring.material as THREE.LineBasicMaterial).dispose());
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
