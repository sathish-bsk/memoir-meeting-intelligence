"use client";

/**
 * Animated background for auth pages — ripple sources converge to a
 * pulsing core, with insight particles emanating from the convergence
 * point. Ported from the v2 design mockup (memoir-auth-v2.jsx lines
 * 23-145).
 *
 * Respects `prefers-reduced-motion`: paints a single static gradient
 * frame instead of running the rAF loop.
 */

import { useEffect, useRef } from "react";

type Source = { x: number; y: number; phase: number; freq: number; color: [number, number, number] };
type Particle = { x: number; y: number; vx: number; vy: number; life: number; decay: number; size: number };

export function SonarCanvas() {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      canvas.width = canvas.offsetWidth * dpr;
      canvas.height = canvas.offsetHeight * dpr;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    const w = () => canvas.offsetWidth;
    const h = () => canvas.offsetHeight;

    // Static fallback: one gradient frame, no animation loop.
    if (reduceMotion) {
      const cx = w() * 0.32;
      const cy = h() * 0.48;
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(w(), h()) * 0.6);
      grad.addColorStop(0, "rgba(92, 92, 224, 0.18)");
      grad.addColorStop(0.5, "rgba(92, 92, 224, 0.06)");
      grad.addColorStop(1, "rgba(92, 92, 224, 0)");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w(), h());
      return () => window.removeEventListener("resize", resize);
    }

    const sources: Source[] = [
      { x: 0.15, y: 0.3, phase: 0, freq: 0.008, color: [92, 92, 224] },
      { x: 0.08, y: 0.7, phase: 2, freq: 0.006, color: [123, 123, 245] },
      { x: 0.35, y: 0.85, phase: 4, freq: 0.007, color: [92, 92, 224] },
      { x: 0.25, y: 0.15, phase: 1, freq: 0.009, color: [123, 123, 245] },
      { x: 0.45, y: 0.5, phase: 3, freq: 0.005, color: [72, 72, 184] },
    ];

    const particles: Particle[] = [];
    const spawnParticle = () => {
      if (particles.length > 25) return;
      const cx = w() * 0.32;
      const cy = h() * 0.48;
      const angle = Math.random() * Math.PI * 2;
      const speed = Math.random() * 0.5 + 0.3;
      particles.push({
        x: cx,
        y: cy,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 1,
        decay: Math.random() * 0.008 + 0.004,
        size: Math.random() * 2 + 1,
      });
    };

    let frame = 0;
    let t = 0;
    let lastDrawTime = 0;
    const FRAME_INTERVAL_MS = 1000 / 30; // throttle to ~30fps

    // Cached convergence gradient — created at canvas origin (0,0), the
    // draw call translates the context to (cx,cy) before painting. This
    // avoids ~30 createRadialGradient calls per second (the single most
    // expensive op in the original loop).
    let convergenceGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, 80);
    convergenceGrad.addColorStop(0, "rgba(92, 92, 224, 0.15)");
    convergenceGrad.addColorStop(0.5, "rgba(92, 92, 224, 0.05)");
    convergenceGrad.addColorStop(1, "rgba(92, 92, 224, 0)");

    // Rebuild the gradient on resize — the canvas context's internal
    // state can be invalidated by setTransform in resize().
    const rebuildGradient = () => {
      convergenceGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, 80);
      convergenceGrad.addColorStop(0, "rgba(92, 92, 224, 0.15)");
      convergenceGrad.addColorStop(0.5, "rgba(92, 92, 224, 0.05)");
      convergenceGrad.addColorStop(1, "rgba(92, 92, 224, 0)");
    };
    window.addEventListener("resize", rebuildGradient);

    const draw = (now: number) => {
      // Pause when tab is hidden — save CPU + keep cache warm.
      if (document.hidden) {
        frame = requestAnimationFrame(draw);
        return;
      }
      // 30fps cap — skip the frame if not enough time has elapsed.
      if (now - lastDrawTime < FRAME_INTERVAL_MS) {
        frame = requestAnimationFrame(draw);
        return;
      }
      lastDrawTime = now;
      t++;
      ctx.clearRect(0, 0, w(), h());

      // Ripple rings per source.
      sources.forEach((src) => {
        const sx = src.x * w();
        const sy = src.y * h();
        const maxR = Math.max(w(), h()) * 0.6;
        for (let ring = 0; ring < 6; ring++) {
          const progress = ((t * src.freq + src.phase + ring * 0.18) % 1);
          const r = progress * maxR;
          const alpha = (1 - progress) * 0.08;
          ctx.beginPath();
          ctx.arc(sx, sy, r, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${src.color[0]},${src.color[1]},${src.color[2]},${alpha})`;
          ctx.lineWidth = 1.5 - progress;
          ctx.stroke();
        }
      });

      // Convergence point — use the cached gradient + globalAlpha to
      // vary intensity per pulse instead of recreating the gradient.
      const cx = w() * 0.32;
      const cy = h() * 0.48;
      const pulse = Math.sin(t * 0.03) * 0.3 + 0.7;

      ctx.save();
      ctx.translate(cx, cy);
      ctx.globalAlpha = pulse;
      ctx.fillStyle = convergenceGrad;
      ctx.beginPath();
      ctx.arc(0, 0, 80, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.fillStyle = `rgba(92, 92, 224, ${0.5 + pulse * 0.3})`;
      ctx.beginPath();
      ctx.arc(cx, cy, 4 + pulse * 2, 0, Math.PI * 2);
      ctx.fill();

      if (Math.random() < 0.06) spawnParticle();
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.life -= p.decay;
        if (p.life <= 0) {
          particles.splice(i, 1);
          continue;
        }
        ctx.fillStyle = `rgba(123, 123, 245, ${p.life * 0.6})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        ctx.fill();
      }

      // Subtle horizontal scan line.
      const scanY = (t * 0.5) % h();
      const scanGrad = ctx.createLinearGradient(0, scanY - 30, 0, scanY + 30);
      scanGrad.addColorStop(0, `rgba(92, 92, 224, 0)`);
      scanGrad.addColorStop(0.5, `rgba(92, 92, 224, 0.03)`);
      scanGrad.addColorStop(1, `rgba(92, 92, 224, 0)`);
      ctx.fillStyle = scanGrad;
      ctx.fillRect(0, scanY - 30, w(), 60);

      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      window.removeEventListener("resize", rebuildGradient);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    />
  );
}
