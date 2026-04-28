const path = require('path');
const { readFile } = require('fs/promises');
import express from 'express';

export function createServer(port) {
  const app = express();
  return app;
}

export const DEFAULT_PORT = 3000;
