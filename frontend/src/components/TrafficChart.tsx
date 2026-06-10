import { useEffect, useRef, useCallback, useState } from 'preact/hooks';
import { i18next } from '../i18n';
import { uiStore } from '../stores/uiStore';
import UPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

interface TrafficChartProps {
  data: { upload: number[]; download: number[]; timestamps: number[] };
  height?: number;
  uploadLabel?: string;
  downloadLabel?: string;
}

export function TrafficChart({ data, height = 160, uploadLabel = 'Upload', downloadLabel = 'Download' }: TrafficChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<UPlot | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const labelsRef = useRef({ uploadLabel, downloadLabel });
  const dataRef = useRef(data);
  const [chartError, setChartError] = useState(false);

  // Keep refs in sync with props
  labelsRef.current = { uploadLabel, downloadLabel };
  dataRef.current = data;

  const getIsDark = useCallback(() => {
    return uiStore.theme.value === 'dark' || (uiStore.theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }, []);

  const createChart = useCallback((width: number, h: number) => {
    if (!chartRef.current) return;
    // Guard against zero or negative dimensions
    if (width <= 0 || h <= 0) return;

    // Destroy existing instance
    if (plotRef.current) {
      try { plotRef.current.destroy(); } catch (_) { /* ignore */ }
      plotRef.current = null;
    }

    const isDark = getIsDark();
    const axisColor = isDark ? '#9ca3af' : '#6b7280';
    const gridColor = isDark ? 'rgba(75, 85, 99, 0.3)' : 'rgba(156, 163, 175, 0.2)';

    const opts: UPlot.Options = {
      width,
      height: h,
      cursor: { drag: { x: true, y: true } },
      scales: { x: { time: true }, y: { auto: true, min: 0 } },
      series: [
        {},
        {
          label: labelsRef.current.uploadLabel,
          stroke: '#10b981',
          width: 2,
          fill: 'rgba(16, 185, 129, 0.1)',
          show: true,
        },
        {
          label: labelsRef.current.downloadLabel,
          stroke: '#f59e0b',
          width: 2,
          fill: 'rgba(245, 158, 11, 0.1)',
          show: true,
        },
      ],
      axes: [
        {
          stroke: axisColor,
          grid: { stroke: gridColor },
          show: false,
        },
        {
          stroke: axisColor,
          grid: { stroke: gridColor },
          values: (u: any, vals: number[]) => vals.map(v => formatChartValue(v)),
        },
      ],
      hooks: {},
    };

    const currentData = dataRef.current;
    // uPlot requires at least one data point to initialize
    let uData: UPlot.AlignedData;
    if (currentData.timestamps.length > 0) {
      uData = [currentData.timestamps, currentData.upload, currentData.download];
    } else {
      // Placeholder: two points at current time with zero values
      // Using two points ensures uPlot has valid aligned data for rendering
      const now = Date.now() / 1000;
      uData = [[now - 1, now], [0, 0], [0, 0]];
    }

    try {
      // Clear any existing content in the container first
      while (chartRef.current.firstChild) {
        chartRef.current.removeChild(chartRef.current.firstChild);
      }
      plotRef.current = new UPlot(opts, uData, chartRef.current);
      sizeRef.current = { width, height: h };
      setChartError(false);
    } catch (e) {
      console.error('uPlot creation failed:', e);
      setChartError(true);
    }
  }, [getIsDark, height]);

  // Format byte rates for chart axis labels
  function formatChartValue(bytesPerSec: number): string {
    if (bytesPerSec === 0) return '0';
    if (!Number.isFinite(bytesPerSec)) return '0';
    const k = 1024;
    if (bytesPerSec < k) return bytesPerSec.toFixed(0) + ' B/s';
    if (bytesPerSec < k * k) return (bytesPerSec / k).toFixed(1) + ' KB/s';
    return (bytesPerSec / (k * k)).toFixed(1) + ' MB/s';
  }

  // ResizeObserver: respond to container size changes
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;

    let initialized = false;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width: w, height: h } = entry.contentRect;
        if (w > 0 && h > 0) {
          const roundedW = Math.floor(w);
          const roundedH = Math.floor(h);
          sizeRef.current = { width: roundedW, height: roundedH };
          if (plotRef.current) {
            // Chart already exists, just resize
            try {
              plotRef.current.setSize({ width: roundedW, height: roundedH });
            } catch (e) {
              // Resize failed, recreate
              createChart(roundedW, roundedH);
            }
          } else if (!initialized) {
            // First valid size — create chart
            initialized = true;
            createChart(roundedW, roundedH);
          }
        }
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [createChart]);

  // Fallback: if chart hasn't been created after a short delay, try directly
  useEffect(() => {
    const timer = setTimeout(() => {
      if (plotRef.current || !chartRef.current) return;
      const rect = chartRef.current.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        createChart(Math.floor(rect.width), Math.floor(rect.height));
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [createChart]);

  // Debounced theme change — recreate chart with updated axis colors
  const themeTimerRef = useRef<ReturnType<typeof requestAnimationFrame> | null>(null);
  useEffect(() => {
    if (!plotRef.current) return;

    if (themeTimerRef.current) {
      cancelAnimationFrame(themeTimerRef.current);
    }
    themeTimerRef.current = requestAnimationFrame(() => {
      const { width, height: h } = sizeRef.current;
      if (width > 0 && h > 0) {
        createChart(width, h);
      }
    });

    return () => {
      if (themeTimerRef.current) {
        cancelAnimationFrame(themeTimerRef.current);
      }
    };
  }, [uiStore.theme.value, getIsDark, createChart]);

  // Update series labels when i18n changes (no chart recreation needed)
  useEffect(() => {
    if (!plotRef.current) return;
    try {
      const series = plotRef.current.series;
      if (series[1]) series[1].label = uploadLabel;
      if (series[2]) series[2].label = downloadLabel;
      plotRef.current.redraw();
    } catch (_) { /* ignore */ }
  }, [uploadLabel, downloadLabel]);

  // Throttled data updates (200ms) to prevent excessive redraws
  const lastDataUpdateRef = useRef(0);
  useEffect(() => {
    if (!plotRef.current) return;
    if (data.timestamps.length === 0) return;

    const now = Date.now();
    if (now - lastDataUpdateRef.current < 200) return;
    lastDataUpdateRef.current = now;

    try {
      const uData: UPlot.AlignedData = [data.timestamps, data.upload, data.download];
      plotRef.current.setData(uData);
    } catch (e) {
      // setData can fail if data shape is invalid, log but don't crash
      console.debug('TrafficChart setData error:', e);
    }
  }, [data]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (plotRef.current) {
        try { plotRef.current.destroy(); } catch (_) { /* ignore */ }
        plotRef.current = null;
      }
    };
  }, []);

  const hasData = data.timestamps.length > 0;

  return (
    // Wrapper: explicit height ensures the container has non-zero dimensions
    // even before uPlot renders, preventing 0-size initialization issues.
    <div style={{ width: '100%', height, position: 'relative' }}>
      {/* Chart canvas container -- uPlot appends its DOM here exclusively.
          Separating this from overlay elements prevents uPlot layout interference. */}
      <div ref={chartRef} style={{ width: '100%', height }} />
      {/* No-data overlay -- outside the uPlot container to avoid interference.
          pointer-events-none ensures it never blocks chart interaction,
          even during brief transitions from empty to populated data. */}
      {!hasData && !chartError && (
        <div class="absolute inset-0 flex items-center justify-center text-gray-400 dark:text-gray-500 text-sm pointer-events-none z-10">
          {i18next.t('dashboard.no_traffic_data')}
        </div>
      )}
      {/* Chart error fallback */}
      {chartError && (
        <div class="absolute inset-0 flex items-center justify-center text-red-400 dark:text-red-500 text-sm pointer-events-none z-10">
          Chart unavailable
        </div>
      )}
    </div>
  );
}
