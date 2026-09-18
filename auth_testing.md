# Auth-Gated App Testing Playbook (Emergent Google Auth)

## Flow
1. Login button → `window.location.href = https://auth.emergentagent.com/?redirect=${encodeURIComponent(window.location.origin + '/')}` (REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH)
2. After Google auth, user lands at `{redirect}#session_id={session_id}`
3. Frontend AppRouter detects `location.hash` containing `session_id=` synchronously during render → renders `<AuthCallback />` (never via useEffect / window.location.hash)
4. AuthCallback (useRef processed-flag) POSTs session_id to backend `/api/auth/google/session` → backend calls Emergent `GET https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data` with header `X-Session-ID`, provisions/maps user, sets auth cookies → frontend navigates to `/` with user in location.state
5. AuthProvider skips `/api/auth/me` check when `window.location.hash` includes `session_id=` (race-condition fix)

## Step 1: Create Test User & Session (Mongo)
```bash
mongosh --eval "
use('<DB_NAME>');
var userId = 'test-user-' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  id: userId,
  email: 'test.user.' + Date.now() + '@example.com',
  name: 'Test User',
  role: 'security',
  status: 'active',
  auth_provider: 'google',
  created_at: new Date().toISOString()
});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000),
  created_at: new Date()
});
print('Session token: ' + sessionToken);
"
```

## Step 2: Backend API
```bash
curl -X GET "<BACKEND_URL>/api/auth/me" -H "Authorization: Bearer <SESSION_TOKEN>"
curl -X GET "<BACKEND_URL>/api/vehicles" -H "Authorization: Bearer <SESSION_TOKEN>"
```

## Step 3: Browser Testing (Playwright)
```javascript
await page.context.add_cookies([{
    "name": "session_token",
    "value": "<SESSION_TOKEN>",
    "domain": "<app-domain>",
    "path": "/",
    "httpOnly": true,
    "secure": true,
    "sameSite": "None"
}]);
await page.goto("<APP_URL>");
```

## Checklist
- [ ] User doc has custom `id` (UUID), never expose Mongo `_id`
- [ ] Session user_id matches user id exactly
- [ ] All user queries exclude `_id` (`{"_id": 0}`)
- [ ] `/api/auth/me` returns user data with Bearer session_token AND with existing JWT cookie
- [ ] Dashboard loads without redirect for Google session
- [ ] Password login still works (admin@rdx.com / admin123)
- [ ] Callback detection uses `useLocation().hash`, not `window.location.hash`
- [ ] AuthProvider skips /me when hash contains session_id=
- [ ] Unknown Google emails are rejected (allowlist) or provisioned per configured policy
- [ ] ADMIN role behavior unchanged

## Debug
```bash
mongosh --eval "use('<DB_NAME>'); db.users.find({auth_provider:'google'}).limit(2); db.user_sessions.find().limit(2);"
# cleanup
mongosh --eval "use('<DB_NAME>'); db.users.deleteMany({email: /test\.user\./}); db.user_sessions.deleteMany({session_token: /test_session/});"
```
