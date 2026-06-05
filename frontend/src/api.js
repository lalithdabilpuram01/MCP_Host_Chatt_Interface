const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function sendChatMessage(message, history, approval = {}) {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, history, ...approval }),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "The backend could not process the chat request.");
  }

  return data;
}

export async function getSettings() {
  const response = await fetch(`${API_BASE_URL}/api/settings`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not load settings.");
  }
  return data;
}

export async function saveSettings(settings) {
  const response = await fetch(`${API_BASE_URL}/api/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(settings),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not save settings.");
  }
  return data;
}

export async function deleteSettings() {
  const response = await fetch(`${API_BASE_URL}/api/settings`, {
    method: "DELETE",
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Could not delete settings.");
  }
  return data;
}
