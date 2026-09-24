import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { ReviewDetail } from './pages/ReviewDetail';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/review/:taskId" element={<ReviewDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
