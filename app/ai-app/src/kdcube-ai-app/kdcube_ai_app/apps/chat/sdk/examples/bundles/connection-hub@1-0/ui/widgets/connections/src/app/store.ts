import { configureStore } from '@reduxjs/toolkit';
import authenticatorsReducer from '../features/authenticators/authenticatorsSlice';
import connectionsReducer from '../features/connections/connectionsSlice';
import delegatedAccessReducer from '../features/delegatedAccess/delegatedAccessSlice';
import emailReducer from '../features/email/emailSlice';
import identityReducer from '../features/identity/identitySlice';

export const store = configureStore({
  reducer: {
    authenticators: authenticatorsReducer,
    connections: connectionsReducer,
    delegatedAccess: delegatedAccessReducer,
    email: emailReducer,
    identity: identityReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
