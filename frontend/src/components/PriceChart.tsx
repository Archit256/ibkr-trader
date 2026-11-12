import { useMemo, useState } from "react";
import type { PriceBar, Quote } from "../types";

interface PriceChartProps {
  symbol: string;
  bars: PriceBar[];
  quote?: Quote;
}

function calcSma(values: number[], period: number): (number | null)[] {
  return values.map((_, idx) => {
    if (idx + 1 < period) return null;
    const slice = values.slice(idx + 1 - period, idx + 1);
    return slice.reduce((a, b) => a + b, 0) / period;
  });
}

export function PriceChart({ symbol, bars, quote }: PriceChartProps) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const chart = useMemo(() => {
    if (bars.length === 0) return null;
    const width = 760;
    const height = 270;
    const padL = 12;
    const padR = 58;
    const padT = 16;
    const padB = 26;

    const highs = bars.map((b) => b.high);
    const lows = bars.map((b) => b.low);
    const maxP = Math.max(...highs);
    const minP = Math.min(...lows);
    const span = Math.max(0.01, maxP - minP);
    const yMax = maxP + span * 0.08;
    const yMin = minP - span * 0.08;

    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const xStep = bars.length > 1 ? plotW / (bars.length - 1) : plotW;
    const toX = (i: number) => padL + i * xStep;
    const toY = (p: number) => padT + ((yMax - p) / (yMax - yMin)) * plotH;

    const closes = bars.map((b) => b.close);
    const sma5 = calcSma(closes, 5);
    const sma15 = calcSma(closes, 15);

    const buildLine = (series: (number | null)[]) => {
      let d = "";
      series.forEach((val, idx) => {
        if (val === null) return;
        const cmd = d === "" ? "M" : "L";
        d += `${cmd}${toX(idx).toFixed(1)},${toY(val).toFixed(1)} `;
      });
      return d.trim();
    };

    const gridTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => {
      const price = yMax - t * (yMax - yMin);
      return { y: padT + t * plotH, price };
    });

    const isUp = bars[bars.length - 1].close >= bars[0].open;
    const candleW = Math.max(3, Math.min(9, plotW / bars.length - 2));

    return {
      width,
      height,
      padL,
      padR,
      padT,
      padB,
      plotW,
      plotH,
      toX,
      toY,
      sma5Path: buildLine(sma5),
      sma15Path: buildLine(sma15),
      gridTicks,
      isUp,
      candleW,
    };
  }, [bars]);

  const activeBar =
    hoverIdx !== null && bars[hoverIdx] ? bars[hoverIdx] : bars[bars.length - 1];

  return (
    <div className="card chart-card">
      <div className="chart-header">
        <div className="chart-title-group">
          <span className="symbol-pill">{symbol}</span>
          {quote && (
            <>
              <span className="price-big">${quote.last.toFixed(2)}</span>
              <span className={`delta-badge ${quote.change_pct >= 0 ? "pos" : "neg"}`}>
                {quote.change_pct >= 0 ? "+" : ""}
                {quote.change_pct.toFixed(2)}%
              </span>
              <span className="muted-sm">
                Bid ${quote.bid.toFixed(2)} · Ask ${quote.ask.toFixed(2)}
              </span>
            </>
          )}
        </div>
        <div className="chart-legend">
          <span className="legend-item">
            <i className="dot dot-fast" /> SMA 5
          </span>
          <span className="legend-item">
            <i className="dot dot-slow" /> SMA 15
          </span>
        </div>
      </div>

      {activeBar && (
        <div className="ohlc-strip">
          <span>T: {activeBar.time}</span>
          <span>O: ${activeBar.open.toFixed(2)}</span>
          <span>H: ${activeBar.high.toFixed(2)}</span>
          <span>L: ${activeBar.low.toFixed(2)}</span>
          <span>C: ${activeBar.close.toFixed(2)}</span>
          <span>Vol: {activeBar.volume.toLocaleString()}</span>
        </div>
      )}

      {!chart ? (
        <div className="empty-box">Waiting for market bars...</div>
      ) : (
        <svg
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          className="price-svg"
          onMouseLeave={() => setHoverIdx(null)}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const relX = ((e.clientX - rect.left) / rect.width) * chart.width;
            const idx = Math.round(
              ((relX - chart.padL) / Math.max(1, chart.plotW)) * (bars.length - 1)
            );
            setHoverIdx(Math.max(0, Math.min(bars.length - 1, idx)));
          }}
        >
          {chart.gridTicks.map((tick, idx) => (
            <g key={idx}>
              <line
                x1={chart.padL}
                x2={chart.width - chart.padR}
                y1={tick.y}
                y2={tick.y}
                className="chart-grid"
              />
              <text
                x={chart.width - chart.padR + 6}
                y={tick.y + 4}
                className="chart-axis-text"
              >
                {tick.price.toFixed(2)}
              </text>
            </g>
          ))}

          {bars.map((b, idx) => {
            const x = chart.toX(idx);
            const yHigh = chart.toY(b.high);
            const yLow = chart.toY(b.low);
            const yOpen = chart.toY(b.open);
            const yClose = chart.toY(b.close);
            const bull = b.close >= b.open;
            const bodyTop = Math.min(yOpen, yClose);
            const bodyH = Math.max(1.5, Math.abs(yOpen - yClose));
            return (
              <g key={`${b.time}-${idx}`}>
                <line
                  x1={x}
                  x2={x}
                  y1={yHigh}
                  y2={yLow}
                  className={bull ? "wick-up" : "wick-down"}
                />
                <rect
                  x={x - chart.candleW / 2}
                  y={bodyTop}
                  width={chart.candleW}
                  height={bodyH}
                  rx={1}
                  className={bull ? "candle-up" : "candle-down"}
                />
              </g>
            );
          })}

          {chart.sma15Path && <path d={chart.sma15Path} className="sma-slow-line" />}
          {chart.sma5Path && <path d={chart.sma5Path} className="sma-fast-line" />}

          {hoverIdx !== null && bars[hoverIdx] && (
            <line
              x1={chart.toX(hoverIdx)}
              x2={chart.toX(hoverIdx)}
              y1={chart.padT}
              y2={chart.height - chart.padB}
              className="crosshair-line"
            />
          )}
        </svg>
      )}
    </div>
  );
}

