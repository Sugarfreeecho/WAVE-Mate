# Frontend Workflow

The frontend has one source of truth:

```text
frontend/src/ -> npm run build -> app/templates/dist/
```

Rules:

1. Make UI behavior and style changes in `frontend/src/`.
2. Treat `app/templates/dist/` as generated output.
3. Run `npm run build` from `frontend/` after every frontend source change.
4. Do not hand-edit `app/templates/dist/assets/index-*.js` or `index-*.css`.
5. If a temporary hotfix is made in `dist`, move the change back to
   `frontend/src/` and rebuild before considering it finished.

The backend serves `app/templates/dist/index.html`, so source changes are not
visible in the running app until the Vite build has regenerated the dist files.
