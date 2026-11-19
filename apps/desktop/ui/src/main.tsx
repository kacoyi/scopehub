import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartWorkspace } from './chart-workspace';
import { readSampleView, saveSampleView } from './sample-view';
import './style.css';

function initialSource(): boolean {
  if (window.location.hash) return window.location.hash === '#demo';
  return readSampleView() !== undefined;
}

function App() {
  const [sample, setSample] = useState(initialSource);
  useEffect(() => {
    const navigate = () => {
      const chosen = window.location.hash === '#demo';
      if (!chosen) saveSampleView(null);
      setSample(chosen);
    };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);
  const leaveSample = () => {
    saveSampleView(null);
    if (window.location.hash) window.location.hash = '';
    setSample(false);
  };
  return <ChartWorkspace key={sample ? 'sample' : 'unconfigured'} sample={sample} leaveSample={leaveSample} />;
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
createRoot(root).render(<App />);
