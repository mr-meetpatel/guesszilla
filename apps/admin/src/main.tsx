import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { App } from './shell';
import './styles.css';

const client = new QueryClient({
  defaultOptions: {
    // Queries refresh on navigation/focus, while pages that need live updates
    // opt into polling explicitly. Avoid polling every admin endpoint globally.
    queries: { retry: false },
  },
});
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
