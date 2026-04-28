import { UserModel } from './db';
import bcrypt from 'bcrypt';
import type { Session } from './types';

export interface LoginOptions {
  rememberMe?: boolean;
}

/**
 * Authenticates a user and returns a session token.
 */
export async function loginUser(
  email: string,
  password: string,
  options?: LoginOptions
): Promise<Session> {
  throw new Error('not implemented');
}

export class AuthService {
  private db: UserModel;

  constructor(db: UserModel) {
    this.db = db;
  }

  async validateSession(token: string): Promise<boolean> {
    return false;
  }
}

export const TIMEOUT_MS = 3000;
export type UserId = string;
export * from './helpers';
