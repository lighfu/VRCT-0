import { useStdoutToPython } from "@useStdoutToPython";
import {
    useStore_IsLMStudioConnected,
    useStore_IsOllamaConnected,
    useStore_IsAiCliConnected,
} from "@store";

export const useLLMConnection = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const {
        currentIsLMStudioConnected,
        updateIsLMStudioConnected,
        pendingIsLMStudioConnected,
    } = useStore_IsLMStudioConnected();
    const {
        currentIsOllamaConnected,
        updateIsOllamaConnected,
        pendingIsOllamaConnected,
    } = useStore_IsOllamaConnected();
    const {
        currentIsAiCliConnected,
        updateIsAiCliConnected,
        pendingIsAiCliConnected,
    } = useStore_IsAiCliConnected();

    const checkConnection_LMStudio = () => {
        pendingIsLMStudioConnected();
        asyncStdoutToPython("/run/lmstudio_connection");
    };
    const setConnectionStatus_LMStudio = (is_connected) => {
        updateIsLMStudioConnected(is_connected);
    };

    const checkConnection_Ollama = () => {
        pendingIsOllamaConnected();
        asyncStdoutToPython("/run/ollama_connection");
    };
    const setConnectionStatus_Ollama = (is_connected) => {
        updateIsOllamaConnected(is_connected);
    };

    const checkConnection_AiCli = () => {
        pendingIsAiCliConnected();
        asyncStdoutToPython("/run/ai_cli_connection");
    };
    const setConnectionStatus_AiCli = (is_connected) => {
        updateIsAiCliConnected(is_connected);
    };

    return {
        currentIsLMStudioConnected,
        updateIsLMStudioConnected,
        setConnectionStatus_LMStudio,
        checkConnection_LMStudio,

        currentIsOllamaConnected,
        updateIsOllamaConnected,
        setConnectionStatus_Ollama,
        checkConnection_Ollama,

        currentIsAiCliConnected,
        updateIsAiCliConnected,
        setConnectionStatus_AiCli,
        checkConnection_AiCli,
    };
};