import { useEffect, useRef } from "react";

// Globe canvas animation — ported from aaagents.de website
export const ChartBackground = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const C = canvasRef.current;
    if (!C) return;
    const X = C.getContext("2d");
    if (!X) return;

    let W = 0, H = 0;
    const mouse = { x: 9999, y: 9999 };
    let sY = 0, cP = 0;

    function resize() {
      W = C!.width = window.innerWidth;
      H = C!.height = window.innerHeight;
    }
    resize();

    const onResize = () => resize();
    const onScroll = () => { sY = window.scrollY; };
    const onMouseMove = (e: MouseEvent) => { mouse.x = e.clientX; mouse.y = e.clientY; };
    const onMouseLeave = () => { mouse.x = 9999; mouse.y = 9999; };

    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll);
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseleave", onMouseLeave);

    const LAT = 14, LON = 20, GR = 260;
    const gP: number[][] = [];
    const latL: number[][][] = [];
    const lonL: number[][][] = [];

    for (let la = 0; la < LAT; la++) {
      const th = Math.PI * (la + 1) / (LAT + 1);
      const ring: number[][] = [];
      for (let i = 0; i <= 40; i++) {
        const ph = Math.PI * 2 * i / 40;
        const pt = [Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)];
        ring.push(pt); gP.push(pt);
      }
      latL.push(ring);
    }
    for (let lo = 0; lo < LON; lo++) {
      const ph = Math.PI * 2 * lo / LON;
      const arc: number[][] = [];
      for (let i = 0; i <= 28; i++) {
        const th = Math.PI * i / 28;
        arc.push([Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)]);
      }
      lonL.push(arc);
    }

    let rY = 0, rX = 0.25, rS = 0.0012, tS = 0.0012;
    let br = 0, oS = GR, tSc = GR;

    function rot(p: number[]): number[] {
      const [px, py, pz] = p;
      let c = Math.cos(rY), s = Math.sin(rY);
      const x1 = px*c - pz*s, z1 = px*s + pz*c;
      c = Math.cos(rX); s = Math.sin(rX);
      return [x1, py*c - z1*s, py*s + z1*c];
    }

    function prj(p: number[]) {
      const sc = oS + Math.sin(br)*2 + cP*20;
      return { x: W/2 + p[0]*sc, y: H/2 + p[1]*sc - sY*0.04, z: p[2] };
    }

    const fragTexts = [
      'BUY','SELL','HOLD','LONG','SHORT','$NVDA','$AAPL','$TSLA','$MSFT','$AMZN',
      '$GOOG','$META','$SPY','$QQQ','DAX','EUR/USD','BTC','LSTM','RL-PPO','GEMINI',
      'LangGraph','FastAPI','WebSocket','React',
      '7/9','8/9','5/9','1.2s','0.9s','1.8s','+3.2%','-1.4%','+0.8%',
      'P&L','RSI','MACD','EMA','SMA','VOL','VWAP','ALPHA','BETA','SHARPE',
      'DRAWDOWN','EQUITY','HEDGE','CONSENSUS','AGENT','SIGNAL','EXECUTE','LATENCY',
    ];

    const frags = Array.from({ length: 30 }, () => ({
      a: Math.random() * Math.PI * 2,
      r: GR + 80 + Math.random() * 140,
      s: 0.0004 + Math.random() * 0.0014,
      yo: (Math.random() - 0.5) * 60,
      t: fragTexts[Math.floor(Math.random() * fragTexts.length)],
      al: 0.04 + Math.random() * 0.07,
      sz: 8 + Math.random() * 4,
    }));

    const pR: Array<{ r: number; m: number; l: number }> = [];

    const onClickPulse = (e: MouseEvent) => {
      const tg = (e.target as HTMLElement).tagName.toLowerCase();
      if (tg === 'a' || tg === 'button' || tg === 'input') return;
      cP = 1;
      pR.push({ r: 0, m: 320 + Math.random() * 100, l: 1 });
    };
    document.addEventListener("click", onClickPulse);

    let animId = 0;

    function draw() {
      X.clearRect(0, 0, W, H);
      br += 0.008;
      const sp = Math.min(sY / (window.innerHeight * 2), 1);
      tSc = GR + sp * 50; tS = 0.0012 + sp * 0.003;
      oS += (tSc - oS) * 0.02; rS += (tS - rS) * 0.02;
      const mx = (mouse.x - W/2)/W, my = (mouse.y - H/2)/H;
      rY += rS + mx*0.0006; rX += rS*0.1 + my*0.0006; cP *= 0.93;
      const cy = H/2 - sY*0.04;
      const dxm = mouse.x - W/2, dym = mouse.y - cy;
      const hD = Math.sqrt(dxm*dxm + dym*dym), hI = Math.max(0, 1 - hD/400);
      const bA = 0.06 + hI*0.08 + cP*0.1;

      for (const ring of latL) {
        X.beginPath();
        ring.forEach((pt, j) => { const r=rot(pt),p=prj(r); if (j===0) { X.moveTo(p.x,p.y); } else { X.lineTo(p.x,p.y); } });
        X.strokeStyle=`rgba(255,255,255,${bA*0.9})`; X.lineWidth=0.5+hI*0.3; X.stroke();
      }
      for (const arc of lonL) {
        X.beginPath();
        arc.forEach((pt, j) => { const r=rot(pt),p=prj(r); if (j===0) { X.moveTo(p.x,p.y); } else { X.lineTo(p.x,p.y); } });
        X.strokeStyle=`rgba(255,255,255,${bA*0.7})`; X.lineWidth=0.4+hI*0.2; X.stroke();
      }
      for (let i=0; i<gP.length; i+=4) {
        const r=rot(gP[i]),p=prj(r);
        const da=0.05+Math.max(0,r[2])*0.15+hI*0.15+cP*0.2;
        const dr=0.8+Math.max(0,r[2])*0.8+hI*1+cP*1.2;
        X.beginPath(); X.arc(p.x,p.y,Math.max(0.3,dr),0,Math.PI*2);
        X.fillStyle=`rgba(255,255,255,${Math.min(da,0.5)})`; X.fill();
      }
      X.beginPath();
      for (let i=0; i<=80; i++) {
        const ph=Math.PI*2*i/80, r=rot([Math.cos(ph),0,Math.sin(ph)]), p=prj(r);
        if (i===0) { X.moveTo(p.x,p.y); } else { X.lineTo(p.x,p.y); }
      }
      X.strokeStyle=`rgba(255,255,255,${0.08+hI*0.07+cP*0.1})`; X.lineWidth=0.8+hI*0.4; X.stroke();
      if (hD < 440) {
        for (let i=0; i<gP.length; i+=6) {
          const r=rot(gP[i]),p=prj(r);
          const dx=p.x-mouse.x, dy=p.y-mouse.y, d=Math.sqrt(dx*dx+dy*dy);
          if (d<110) { X.beginPath(); X.moveTo(mouse.x,mouse.y); X.lineTo(p.x,p.y); X.strokeStyle=`rgba(255,255,255,${(1-d/110)*0.07})`; X.lineWidth=0.3; X.stroke(); }
        }
      }
      const gA=0.025+hI*0.025+cP*0.04;
      const grad=X.createRadialGradient(W/2,cy,0,W/2,cy,oS*1.15);
      grad.addColorStop(0,`rgba(255,255,255,${gA})`); grad.addColorStop(1,"rgba(255,255,255,0)");
      X.fillStyle=grad; X.fillRect(0,0,W,H);
      for (let i=pR.length-1; i>=0; i--) {
        pR[i].r+=1; pR[i].l=Math.max(0,1-pR[i].r/pR[i].m);
        if (pR[i].l>0) { X.beginPath(); X.arc(W/2,H/2-sY*0.04,pR[i].r,0,Math.PI*2); X.strokeStyle=`rgba(255,255,255,${pR[i].l*0.05})`; X.lineWidth=0.6; X.stroke(); }
        if (pR[i].l<=0) pR.splice(i,1);
      }
      for (const f of frags) {
        f.a+=f.s;
        const cx=W/2+Math.cos(f.a)*f.r, fy=H/2+Math.sin(f.a)*f.r*0.3+f.yo-sY*0.04;
        X.font=`500 ${f.sz}px "JetBrains Mono"`; X.textAlign="center";
        X.fillStyle=`rgba(255,255,255,${f.al})`; X.fillText(f.t,cx,fy);
      }
      animId = requestAnimationFrame(draw);
    }
    draw();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseleave", onMouseLeave);
      document.removeEventListener("click", onClickPulse);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-0"
      style={{ display: "block", pointerEvents: "none" }}
    />
  );
};
