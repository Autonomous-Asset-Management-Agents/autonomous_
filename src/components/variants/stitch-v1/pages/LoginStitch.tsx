import { useLoginData } from "@/hooks/useLoginData";

const LoginStitch = () => {
    const { googleLoading, handleGoogle, user } = useLoginData();

    if (user) return null;

    return (
        <div className="flex flex-col items-center justify-center min-h-screen bg-white text-black p-4">
            <h1 className="text-4xl font-bold mb-8">Stitch V1 Login</h1>
            <p className="mb-8 text-gray-500 text-center max-w-md">
                Dies ist das zukünftige, KI-generierte Design. Die Funktionalität (Firebase Auth) wird über denselben Hook geteilt wie in der Legacy Variante!
            </p>
            <button 
                onClick={handleGoogle}
                disabled={googleLoading}
                className="bg-blue-600 text-white px-8 py-3 rounded-xl font-semibold hover:bg-blue-700 transition"
            >
                {googleLoading ? "Laden..." : "Anmelden mit Google"}
            </button>
        </div>
    );
};

export default LoginStitch;
