import { create } from 'zustand';
import { VcuState } from '../types';

interface VcuStore {
  vcuState: VcuState;
  setVcuState: (state: VcuState) => void;
}

export const useVcuStore = create<VcuStore>((set) => ({
  vcuState: 'idle',
  setVcuState: (state) => set({ vcuState: state }),
}));
