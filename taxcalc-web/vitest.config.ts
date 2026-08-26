import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      // The generated GraphQL client, the test suite itself, and MSW's
      // worker/handler fixtures aren't application logic - counting them
      // would dilute the coverage signal with code nobody hand-wrote or
      // that exists only to serve the tests.
      exclude: ['src/gql/generated/**', 'src/test/**', 'src/vite-env.d.ts'],
      thresholds: {
        // The W4 D5 capstone gate. Branch coverage is the load-bearing
        // metric; lines/functions/statements come along for the ride.
        branches: 70,
        lines: 75,
        functions: 75,
        statements: 75,
      },
    },
  },
});
