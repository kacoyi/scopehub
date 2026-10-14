import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

function App() {
  return (
    <main>
      <header>
        <strong>ScopeHub</strong>
        <span role="status">Offline · No data source</span>
      </header>
      <div className="workspace">
        <aside aria-label="AI workspace">
          <h1>Conversation</h1>
          <p>No conversation yet.</p>
        </aside>
        <section aria-label="Chart workspace">
          <div className="empty-state">
            <span className="crosshair" aria-hidden="true">+</span>
            <h2>No instrument selected</h2>
            <p>Your chart and indicator previews will appear here.</p>
          </div>
        </section>
      </div>
    </main>
  );
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
createRoot(root).render(<StrictMode><App /></StrictMode>);
