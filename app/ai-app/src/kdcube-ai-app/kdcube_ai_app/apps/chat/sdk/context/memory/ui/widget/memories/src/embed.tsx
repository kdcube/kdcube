import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Provider } from 'react-redux';
import App from './App';
import { store } from './app/store';
import cssText from './styles.css?inline';

export function MemoriesWidgetEmbed() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [shadowRoot, setShadowRoot] = useState<ShadowRoot | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const root = host.shadowRoot || host.attachShadow({ mode: 'open' });
    if (!root.querySelector('style[data-kdcube-memories]')) {
      const style = document.createElement('style');
      style.setAttribute('data-kdcube-memories', 'true');
      style.textContent = cssText;
      root.appendChild(style);
    }
    setShadowRoot(root);
  }, []);

  return (
    <div className="memories-widget-host" ref={hostRef}>
      {shadowRoot ? createPortal(
        <Provider store={store}>
          <App />
        </Provider>,
        shadowRoot,
      ) : null}
    </div>
  );
}

export default MemoriesWidgetEmbed;
