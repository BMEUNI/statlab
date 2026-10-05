import { defineConfig } from 'vite';

const origin = process.env.STATLAB_API_ORIGIN ?? 'http://127.0.0.1:8000';
let parsedOrigin: URL;
try {
  parsedOrigin = new URL(origin);
} catch {
  throw new Error('STATLAB_API_ORIGIN must be a loopback HTTP origin.');
}
if (
  parsedOrigin.protocol !== 'http:' ||
  !['127.0.0.1', 'localhost', '[::1]'].includes(parsedOrigin.hostname) ||
  parsedOrigin.pathname !== '/' ||
  parsedOrigin.username !== '' ||
  parsedOrigin.password !== '' ||
  parsedOrigin.search !== '' ||
  parsedOrigin.hash !== ''
) {
  throw new Error('STATLAB_API_ORIGIN must be a loopback HTTP origin.');
}

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: origin, changeOrigin: true } },
  },
  build: { target: 'es2022' },
});
