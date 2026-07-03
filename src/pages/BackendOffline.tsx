import { AlertCircle, RefreshCw, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";

const BackendOffline = () => {
  return (
    <div className="min-h-screen bg-black flex flex-col items-center justify-center p-4">
      <div className="max-w-md w-full bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-center space-y-6">
        <div className="mx-auto w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center">
          <AlertCircle className="w-8 h-8 text-red-500" />
        </div>
        
        <div className="space-y-2">
          <h1 className="text-2xl font-bold text-white tracking-tight">
            Backend Offline
          </h1>
          <p className="text-zinc-400 text-sm">
            The autonomous_ Desktop app could not connect to the local Python backend.
          </p>
        </div>

        <div className="bg-black/50 border border-zinc-800 rounded-lg p-4 text-left">
          <div className="flex items-center gap-2 text-zinc-300 mb-2 font-medium">
            <Terminal className="w-4 h-4" />
            <span>How to fix this:</span>
          </div>
          <ol className="list-decimal list-inside text-sm text-zinc-400 space-y-2">
            <li>Open a terminal or PowerShell window.</li>
            <li>Navigate to the autonomous_ installation folder.</li>
            <li>Run <code className="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300">setup.ps1</code> or <code className="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300">docker compose up -d</code>.</li>
            <li>Wait for the containers to become healthy.</li>
          </ol>
        </div>

        <Button 
          onClick={() => window.location.reload()}
          className="w-full bg-white text-black hover:bg-zinc-200"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Retry Connection
        </Button>
      </div>
    </div>
  );
};

export default BackendOffline;
