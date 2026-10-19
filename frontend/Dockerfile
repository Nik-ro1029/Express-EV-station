FROM node:20-alpine

ENV NODE_ENV=production
WORKDIR /app

# install dependencies first so Docker can cache this layer
COPY package*.json ./
RUN npm ci --omit=dev

COPY . .

USER node
EXPOSE 3000
CMD ["node", "server.js"]
