import fs from 'fs';
import path from 'path';

export default function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') { res.status(204).end(); return; }
  if (req.method !== 'GET') { res.status(405).json({ error: 'method not allowed' }); return; }

  try {
    const logPath = path.resolve('engine/data_store/live_engine.log');
    
    if (!fs.existsSync(logPath)) {
      res.status(200).json({ logs: ["Waiting for live trading engine daemon to start logging..."] });
      return;
    }

    const data = fs.readFileSync(logPath, 'utf8');
    const lines = data.split('\n').filter(Boolean);
    // Return the last 150 lines
    const lastLines = lines.slice(-150);
    
    res.status(200).json({ logs: lastLines });
  } catch (error) {
    res.status(500).json({ error: 'failed to read logs', details: String(error) });
  }
}
