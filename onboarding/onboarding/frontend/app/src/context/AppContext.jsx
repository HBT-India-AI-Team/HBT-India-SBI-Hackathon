import { createContext, useCallback, useContext, useEffect, useState } from 'react';

const AppCtx = createContext(null);

const LS_KEY = 'yono3.session';

function loadPersisted() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function AppProvider({ children }) {
  const [state, setState] = useState(() => ({
    applicationId: null,
    sessionId: null,
    application: null,
    language: 'en',
    productId: null,
    mobileNumber: null,
    ...loadPersisted(),
  }));

  useEffect(() => {
    const { applicationId, sessionId, language, productId, mobileNumber } = state;
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({ applicationId, sessionId, language, productId, mobileNumber })
    );
  }, [state.applicationId, state.sessionId, state.language, state.productId, state.mobileNumber]);

  const patch = useCallback((updates) => {
    setState((prev) => ({ ...prev, ...updates }));
  }, []);

  const reset = useCallback(() => {
    localStorage.removeItem(LS_KEY);
    setState({
      applicationId: null,
      sessionId: null,
      application: null,
      language: 'en',
      productId: null,
      mobileNumber: null,
    });
  }, []);

  return <AppCtx.Provider value={{ ...state, patch, reset }}>{children}</AppCtx.Provider>;
}

export function useApp() {
  const ctx = useContext(AppCtx);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}
