import { configureStore } from '@reduxjs/toolkit';
import authenticatorsReducer from '../features/authenticators/authenticatorsSlice';
import accessMapReducer from '../features/accessMap/accessMapSlice';
import dcrAllowlistReducer from '../features/dcrAllowlist/dcrAllowlistSlice';
import delegatedAccessReducer from '../features/delegatedAccess/delegatedAccessSlice';
import identityReducer from '../features/identity/identitySlice';
import delegatedToKdcubeReducer from '../features/delegatedToKdcube/delegatedToKdcubeSlice';
import providerConnectionsReducer from '../features/providerConnections/providerConnectionsSlice';

export const store = configureStore({
  reducer: {
    authenticators: authenticatorsReducer,
    accessMap: accessMapReducer,
    dcrAllowlist: dcrAllowlistReducer,
    delegatedAccess: delegatedAccessReducer,
    identity: identityReducer,
    delegatedToKdcube: delegatedToKdcubeReducer,
    providerConnections: providerConnectionsReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
