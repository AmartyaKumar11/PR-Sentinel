import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
});

export const getReviews = (repo, limit = 20, offset = 0) =>
  api.get('/reviews', { params: { repo, limit, offset } }).then((r) => r.data);

export const getReviewDetail = (taskId) =>
  api.get(`/reviews/${taskId}`).then((r) => r.data);

export const getHealth = () => api.get('/health').then((r) => r.data);

export const DEFAULT_REPO = 'AmartyaKumar11/pr-sentinel-demo';
