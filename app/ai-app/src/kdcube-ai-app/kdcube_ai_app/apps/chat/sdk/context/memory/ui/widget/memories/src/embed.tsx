import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Provider } from 'react-redux';
import App from './App';
import { setMemoryWidgetCallOperation } from './api/client';
import type { MemoryWidgetCallOperation } from './api/client';
import { store } from './app/store';
import cssText from './styles.css?inline';

export interface MemoriesWidgetEmbedProps {
  callOperation?: MemoryWidgetCallOperation;
}

export function MemoriesWidgetEmbed({ callOperation }: MemoriesWidgetEmbedProps = {}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [shadowRoot, setShadowRoot] = useState<ShadowRoot | null>(null);
  const [operationReady, setOperationReady] = useState(!callOperation);

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

  useEffect(() => {
    if (!callOperation) {
      setOperationReady(true);
      return undefined;
    }
    setOperationReady(false);
    return setMemoryWidgetCallOperation(callOperation);
  }, [callOperation]);

  useEffect(() => {
    setOperationReady(true);
  }, [callOperation]);

  return (
    <div className="memories-widget-host" ref={hostRef}>
      {shadowRoot && operationReady ? createPortal(
        <Provider store={store}>
          <App />
        </Provider>,
        shadowRoot,
      ) : null}
    </div>
  );
}

export default MemoriesWidgetEmbed;
