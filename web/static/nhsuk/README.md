Vendored NHS.UK frontend 10.7.2 (MIT licence, github.com/nhsuk/nhsuk-frontend), copied from the npm package
`dist/nhsuk/`. The Frutiger `@font-face` rules were removed so the browser never calls assets.nhs.uk and the
pages fall back to Arial, as the design system allows. Nothing else was changed.
