# Static build served by Nginx. The design prototype in this repository is a
# single self-contained HTML Design Component; a Vite/React app dropped into
# src/ builds through the same stage without changes.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
