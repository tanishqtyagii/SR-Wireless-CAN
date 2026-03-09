import { useEffect } from "react";
import { Route, Routes } from "react-router-dom";
import FlashPage from "./routes/FlashPage";
import LibraryPage from "./routes/LibraryPage";

function App() {
  useEffect(() => {
    const root = document.documentElement;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = (isDark: boolean) => {
      root.classList.toggle("dark", isDark);
    };

    applyTheme(mediaQuery.matches);
    const handler = (event: MediaQueryListEvent) => applyTheme(event.matches);
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, []);

  return (
    <div className="min-h-screen bg-theme-bg font-sans text-theme-text selection:bg-blue-200 dark:selection:bg-blue-900">
      <Routes>
        <Route path="/" element={<FlashPage />} />
        <Route path="/library" element={<LibraryPage />} />
      </Routes>
    </div>
  );
}

export default App;
