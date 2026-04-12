# Frontend

This frontend is a Next.js application intended for Vercel deployment.

## Local Run

```bash
npm install
npm run dev
```

Set the backend URL with:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your_google_oauth_client_id.apps.googleusercontent.com
```

## Deploy To Vercel
- Set the project root to `frontend/`
- Add `NEXT_PUBLIC_API_BASE_URL` in Vercel environment variables
- Add `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in Vercel environment variables
- Deploy as a standard Next.js app
