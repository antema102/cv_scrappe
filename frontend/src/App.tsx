import { Header } from './components/layout/Header';
import { Dashboard } from './pages/Dashboard';
import './index.css';

function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <Dashboard />
    </div>
  );
}

export default App;
