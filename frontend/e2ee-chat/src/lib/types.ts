export type Role = "student" | "faculty";

export type Profile = {
  id: string;
  display_name: string;
  role: Role;
  e2ee_public_key_b64: string | null;
};

export type InboxRow = {
  conversation_id: string;
  other_user_id: string;
  other_display_name: string | null;
  other_role: Role | null;
  other_e2ee_public_key_b64: string | null;
  last_message_id: string | null;
  last_message_at: string | null;
  last_message_sender_id: string | null;
  last_message_type: "text" | "voice" | null;
  last_message_ciphertext_b64: string | null;
  last_message_nonce_b64: string | null;
  unread_count: number;
};

export type MessageRow = {
  id: string;
  conversation_id: string;
  sender_id: string;
  created_at: string;
  client_created_at: string | null;
  type: "text" | "voice";
  ciphertext_b64: string;
  nonce_b64: string;
};

