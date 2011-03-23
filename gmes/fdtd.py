#!/usr/bin/env python

try:
    import psyco
    psyco.profile()
    from psyco.classes import *
except:
    pass

from copy import deepcopy
from threading import Thread, Lock
from numpy import *

from geometry import GeomBoxTree, in_range
from file_io import Probe
#from file_io import write_hdf5, snapshot
from show import ShowLine, ShowPlane, Snapshot
from material import Dummy
import constants as const


class TimeStep(object):
    """Store the current time-step and time.
    
    Attributes:
        n -- current time-step
        t -- current time
        
    """
    def __init__(self, n=0.0, t=0.0):
        self.n = n
        self.t = t
        
        
class FDTD(object):
    """three dimensional finite-difference time-domain class
    
    Attributes:
        space -- geometry.Cartesian instance
        cmplx -- Boolean of whether field is complex. Determined by the 
            space.period.
        
    """
    def __init__(self, space=None, geom_list=None, src_list=None, courant_ratio=.99, dt=None, wavevector=None, verbose=True):
        """
        Argumetns:
        space -- an instance which represents the coordinate system.
        geom_list -- a list which represents the geometric structure.
        src_list -- a list of source instances.
        courant_ratio -- the ratio of dt to Courant stability bound
                         default: 0.99
        dt -- the time differential
              If None is given, dt is calculated using space differentials 
              and courant_ratio.
              default: None
        wavevector -- Bloch wave vector.
        verbose --
        
        """
        self.lock_ex, self.lock_ey = Lock(), Lock()
        self.lock_ez, self.lock_hx = Lock(), Lock()
        self.lock_hy, self.lock_hz = Lock(), Lock()
        self.lock_fig = Lock()
       	
        self.space = space
                
        self.fig_id = self.space.my_id
            
        self.dx, self.dy, self.dz = space.dx, space.dy, space.dz

        stable_limit = self.stable_limit(space)
        # courant_ratio is the ratio of dt to Courant stability bound
        if dt is None:
            self.courant_ratio = float(courant_ratio)
            self.dt = self.courant_ratio * stable_limit
        else:
            self.dt = float(dt)
            self.courant_ratio = self.dt / stable_limit
        # Some codes in geometry.py and source.py use space.dt.
        self.space.dt = self.dt

        self.time_step = TimeStep()
        
        if verbose:
            self.space.display_info()

        if verbose:
            print 'dt:', self.dt
            print 'courant ratio:', self.courant_ratio
            
        if verbose:
            print "Initializing the geometry list...",
            
        self.geom_list = deepcopy(geom_list)
        for geom_obj in self.geom_list:
            geom_obj.init(self.space)
            
        if verbose:
            print "done."
            
        if verbose:
            print "Generating geometric binary search tree...",
            
        self.geom_tree = GeomBoxTree(self.geom_list)

        if verbose:
            print "done."
            
        if verbose:
            print "The geometric tree follows..."
            self.geom_tree.display_info()
                
        if wavevector is None:
            self.cmplx = False
            self.k = None
        else:
            self.cmplx = True

#             # Calculate more accurate k for PBC.
#             from numpy import arcsin
#             ds = array((self.dx, self.dy, self.dz))
#             S = self.courant_ratio
#             self.k = array(wavevector, float)
#             self.k = 2 / ds * arcsin(sin(self.k * S * ds / 2) / S)

            self.k = array(wavevector, float)

        if verbose:
            print "wave vector is", self.k
            
        if verbose:
            print "Initializing source...",
            
        self.src_list = deepcopy(src_list)
        for so in self.src_list:
            so.init(self.geom_tree, self.space, self.cmplx)
            
        if verbose:
            print "done."
            
        if verbose:
            print "The source list information follows..."
            for so in self.src_list:
                so.display_info()
                
        if verbose:
            print "Allocating memory for the electric & magnetic fields...",
            
        # storage for the electric & magnetic field 
        self.ex = space.get_ex_storage(self.cmplx)
        self.ey = space.get_ey_storage(self.cmplx)
        self.ez = space.get_ez_storage(self.cmplx)
        self.hx = space.get_hx_storage(self.cmplx)
        self.hy = space.get_hy_storage(self.cmplx)
        self.hz = space.get_hz_storage(self.cmplx)
        
        if verbose:
            print "done."
            
        if verbose:
            print "ex field:", self.ex.dtype, self.ex.shape 
            print "ey field:", self.ey.dtype, self.ey.shape 
            print "ez field:", self.ez.dtype, self.ez.shape
            print "hx field:", self.hx.dtype, self.hx.shape
            print "hy field:", self.hy.dtype, self.hy.shape
            print "hz field:", self.hz.dtype, self.hz.shape
        
        if verbose:
            print "Mapping the pointwise material...",
            
        self.material_ex = self.material_ey = self.material_ez = None
        self.material_hx = self.material_hy = self.material_hz = None
        
        self.init_material()

        if verbose:
            print "done."
                    # medium information for electric & magnetic fields

        if verbose:
            print "ex material:",
            if self.material_ex is None: print None
            else: print self.material_ex.dtype, self.material_ex.shape
                
            print "ey material:", 
            if self.material_ey is None: print None
            else: print self.material_ey.dtype, self.material_ey.shape
                
            print "ez material:", 
            if self.material_ez is None: print None
            else: print self.material_ez.dtype, self.material_ez.shape
            
            print "hx material:", 
            if self.material_hx is None: print None
            else: print self.material_hx.dtype, self.material_hx.shape
                
            print "hy material:", 
            if self.material_hy is None: print None
            else: print self.material_hy.dtype, self.material_hy.shape
                
            print "hz material:", 
            if self.material_hz is None: print None
            else: print self.material_hz.dtype, self.material_hz.shape            
		
        if verbose:
            print "done."
            
        if verbose:
            print "Mapping the pointwise source...",
            
        self.init_source()

        if verbose:
            print "done."
    
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dx**-2 + space.dy**-2 + space.dz**-2)
        
    def _step_aux_fdtd(self):
        for src in self.src_list:
            src.step()
        
    def __deepcopy__(self, memo={}):
        """The classes generated by swig do not have __deepcopy__ method.
        Thus, the tricky constructor call follows.
        
        """
        newcopy = self.__class__(self.space, self.geom_list, self.src_list, False)
        newcopy.ex = array(self.ex)
        newcopy.ey = array(self.ey)
        newcopy.ez = array(self.ez)
        newcopy.hx = array(self.hx)
        newcopy.hy = array(self.hy)
        newcopy.hz = array(self.hz)
        
        newcopy.time_step = deepcopy(self.time_step)
        return newcopy
    	
    def init_material_ex(self):
        """Set up the update mechanism for Ex field.
        
        Set up the update mechanism for Ex field and stores the result
        at self.material_ex.
        
        """
        self.lock_ex.acquire()
        
        self.material_ex = self.space.get_material_ex_storage()
        shape = self.ex.shape
        for idx in ndindex(shape):
            coords = self.space.ex_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[1] == shape[1] - 1 or idx[2] == shape[2] - 1:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_ex[idx] = \
            mat_obj.get_pw_material_ex(idx, coords, underneath, self.cmplx)
        
        self.lock_ex.release()
        
    def init_material_ey(self):
        """Set up the update mechanism for Ey field.
        
        Set up the update mechanism for Ey field and stores the result
        at self.material_ey.
        
        """
        self.lock_ey.acquire()
        
        self.material_ey = self.space.get_material_ey_storage()
        shape = self.ey.shape
        for idx in ndindex(shape):
            coords = self.space.ey_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[2] == shape[2] - 1 or idx[0] == shape[0] - 1:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_ey[idx] = \
            mat_obj.get_pw_material_ey(idx, coords, underneath, self.cmplx)
            
        self.lock_ey.release()
        
    def init_material_ez(self):
        """Set up the update mechanism for Ez field.
        
        Set up the update mechanism for Ez field and stores the result
        at self.material_ez.
        
        """
        self.lock_ez.acquire()
        
        self.material_ez = self.space.get_material_ez_storage()
        shape = self.ez.shape
        for idx in ndindex(shape):
            coords = self.space.ez_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[0] == shape[0] - 1 or idx[1] == shape[1] - 1:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_ez[idx] = \
            mat_obj.get_pw_material_ez(idx, coords, underneath, self.cmplx)
            
        self.lock_ez.release()
        
    def init_material_hx(self):
        """Set up the update mechanism for Hx field.
        
        Set up the update mechanism for Hx field and stores the result
        at self.material_hx.
        
        """
        self.lock_hx.acquire()
        
        self.material_hx = self.space.get_material_hx_storage()
        shape = self.hx.shape
        for idx in ndindex(shape):
            coords = self.space.hx_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[1] == 0 or idx[2] == 0:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_hx[idx] = \
            mat_obj.get_pw_material_hx(idx, coords, underneath, self.cmplx)
                
        self.lock_hx.release()
        
    def init_material_hy(self):
        """Set up the update mechanism for Hy field.
        
        Set up the update mechanism for Hy field and stores the result
        at self.material_hy.
        
        """
        self.lock_hy.acquire()
        
        self.material_hy = self.space.get_material_hy_storage()
        shape = self.hy.shape
        for idx in ndindex(shape):
            coords = self.space.hy_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[2] == 0 or idx[0] == 0:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_hy[idx] = \
            mat_obj.get_pw_material_hy(idx, coords, underneath, self.cmplx)
            
        self.lock_hy.release()
        
    def init_material_hz(self):
        """Set up the update mechanism for Hz field.
        
        Set up the update mechanism for Hz field and stores the result
        at self.material_hz.
        
        """
        self.lock_hz.acquire()
        
        self.material_hz = self.space.get_material_hz_storage()
        shape = self.hz.shape
        for idx in ndindex(shape):
            coords = self.space.hz_index_to_space(*idx)
            mat_obj, underneath = self.geom_tree.material_of_point(coords)
            if idx[0] == 0 or idx[1] == 0:
                mat_obj = Dummy(mat_obj.epsilon, mat_obj.mu)
            self.material_hz[idx] = \
            mat_obj.get_pw_material_hz(idx, coords, underneath, self.cmplx)
            
        self.lock_hz.release()
        
    def init_material(self):
        self.init_material_ex()
        self.init_material_ey()
        self.init_material_ez()
        self.init_material_hx()
        self.init_material_hy()
        self.init_material_hz()
        
    def init_source_ex(self):
        for so in self.src_list:
            so.set_pointwise_source_ex(self.material_ex, self.space)
            
    def init_source_ey(self):
        for so in self.src_list:
            so.set_pointwise_source_ey(self.material_ey, self.space)
			
    def init_source_ez(self):
        for so in self.src_list:
            so.set_pointwise_source_ez(self.material_ez, self.space)
			
    def init_source_hx(self):
        for so in self.src_list:
            so.set_pointwise_source_hx(self.material_hx, self.space)
			
    def init_source_hy(self):
        for so in self.src_list:
            so.set_pointwise_source_hy(self.material_hy, self.space)
			
    def init_source_hz(self):
        for so in self.src_list:
            so.set_pointwise_source_hz(self.material_hz, self.space)
			
    def init_source(self):
        self.init_source_ex()
        self.init_source_ey()
        self.init_source_ez()
        self.init_source_hx()
        self.init_source_hy()
        self.init_source_hz()
	
    def set_probe(self, x, y, z, prefix):
        if self.material_ex is not None:
            idx = self.space.space_to_ex_index(x, y, z)
            if in_range(idx, self.material_ex, const.Ex):
                self.material_ex[idx] = Probe(prefix + '_ex.dat', self.material_ex[idx])
                loc = self.space.ex_index_to_space(*idx)
                self.material_ex[idx].f.write('# location=' + str(loc) + '\n')
                self.material_ex[idx].f.write('# dt=' + str(self.dt) + '\n')
        
        if self.material_ey is not None:
            idx = self.space.space_to_ey_index(x, y, z)
            if in_range(idx, self.material_ey, const.Ey):
                self.material_ey[idx] = Probe(prefix + '_ey.dat', self.material_ey[idx])
                loc = self.space.ey_index_to_space(*idx)
                self.material_ey[idx].f.write('# location=' + str(loc) + '\n')
                self.material_ey[idx].f.write('# dt=' + str(self.dt) + '\n')
        
        if self.material_ez is not None:
            idx = self.space.space_to_ez_index(x, y, z)
            if in_range(idx, self.material_ez, const.Ez):
                self.material_ez[idx] = Probe(prefix + '_ez.dat', self.material_ez[idx])
                loc = self.space.ez_index_to_space(*idx)
                self.material_ez[idx].f.write('# location=' + str(loc) + '\n')
                self.material_ez[idx].f.write('# dt=' + str(self.dt) + '\n')
        
        if self.material_hx is not None:
            idx = self.space.space_to_hx_index(x, y, z)
            if in_range(idx, self.material_hx, const.Hx):
                self.material_hx[idx] = Probe(prefix + '_hx.dat', self.material_hx[idx])
                loc = self.space.hx_index_to_space(*idx)
                self.material_hx[idx].f.write('# location=' + str(loc) + '\n')
                self.material_hx[idx].f.write('# dt=' + str(self.dt) + '\n')
        
        if self.material_hy is not None:
            idx = self.space.space_to_hy_index(x, y, z)
            if in_range(idx, self.material_hy, const.Hy):
                self.material_hy[idx] = Probe(prefix + '_hy.dat', self.material_hy[idx])
                loc = self.space.hy_index_to_space(*idx)
                self.material_hy[idx].f.write('# location=' + str(loc) + '\n')
                self.material_hy[idx].f.write('# dt=' + str(self.dt) + '\n')
        
        if self.material_hz is not None:
            idx = self.space.space_to_hz_index(x, y, z)
            if in_range(idx, self.material_hz, const.Hz):
                self.material_hz[idx] = Probe(prefix + '_hz.dat', self.material_hz[idx])
                loc = self.space.hz_index_to_space(*idx)
                self.material_hz[idx].f.write('# location=' + str(loc) + '\n')
                self.material_hz[idx].f.write('# dt=' + str(self.dt) + '\n')
            
    def update_ex(self):
        self.lock_ex.acquire()
        for mo in self.material_ex.flat:
            mo.update(self.ex, self.hz, self.hy, self.dy, self.dz, self.dt, self.time_step.n)
        self.lock_ex.release()
        
    def update_ey(self):
        self.lock_ey.acquire()
        for mo in self.material_ey.flat:
            mo.update(self.ey, self.hx, self.hz, self.dz, self.dx, self.dt, self.time_step.n)
        self.lock_ey.release()
		
    def update_ez(self):
        self.lock_ez.acquire()
        for mo in self.material_ez.flat:
            mo.update(self.ez, self.hy, self.hx, self.dx, self.dy, self.dt, self.time_step.n)
        self.lock_ez.release()
		
    def update_hx(self):
        self.lock_hx.acquire()
        for mo in self.material_hx.flat:
            mo.update(self.hx, self.ez, self.ey, self.dy, self.dz, self.dt, self.time_step.n)
        self.lock_hx.release()
		
    def update_hy(self):
        self.lock_hy.acquire()
        for mo in self.material_hy.flat:
            mo.update(self.hy, self.ex, self.ez, self.dz, self.dx, self.dt, self.time_step.n)
        self.lock_hy.release()
		
    def update_hz(self):
        self.lock_hz.acquire()
        for mo in self.material_hz.flat:
            mo.update(self.hz, self.ey, self.ex, self.dx, self.dy, self.dt, self.time_step.n)
        self.lock_hz.release()

    def talk_with_ex_neighbors(self):
        """Synchronize ex data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send ex field data to -y direction and receive from +y direction.
        src, dest = self.space.cart_comm.Shift(1, -1)
        
        if self.cmplx:
            dest_spc = self.space.ex_index_to_space(0, self.ex.shape[1] - 1, 0)[1]
        
            src_spc = self.space.ex_index_to_space(0, 0, 0)[1]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ex.tag,
                                                    None, src, const.Ex.tag)
            
            phase_shift = exp(1j * self.k[1] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.ex[:, -1, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ex[:, 0, :], dest, const.Ex.tag,
                                      None, src, const.Ex.tag)
        
        # send ex field data to -z direction and receive from +z direction.
        src, dest = self.space.cart_comm.Shift(2, -1)
        
        if self.cmplx:
            dest_spc = self.space.ex_index_to_space(0, 0, self.ex.shape[2] - 1)[2]
        
            src_spc = self.space.ex_index_to_space(0, 0, 0)[2]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ex.tag,
                                                    None, src, const.Ex.tag)
        
            phase_shift = exp(1j * self.k[2] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.ex[:, :, -1] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ex[:, :, 0], dest, const.Ex.tag,
                                      None, src, const.Ex.tag)
        
    def talk_with_ey_neighbors(self):
        """Synchronize ey data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send ey field data to -z direction and receive from +z direction.
        src, dest = self.space.cart_comm.Shift(2, -1)
        
        if self.cmplx:
            dest_spc = self.space.ey_index_to_space(0, 0, self.ey.shape[2] - 1)[2]
        
            src_spc = self.space.ey_index_to_space(0, 0, 0)[2]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ey.tag,
                                                    None, src, const.Ey.tag) 
        
            phase_shift = exp(1j * self.k[2] * (dest_spc - src_spc))
        else:
            phase_shift = 0
            
        self.ey[:, :, -1] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ey[:, :, 0], dest, const.Ey.tag,
                                      None, src, const.Ey.tag)
        
        # send ey field data to -x direction and receive from +x direction.
        src, dest = self.space.cart_comm.Shift(0, -1)

        if self.cmplx:
            dest_spc = self.space.ey_index_to_space(self.ey.shape[0] - 1, 0, 0)[0]
        
            src_spc = self.space.ey_index_to_space(0, 0, 0)[0]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ey.tag,
                                                    None, src, const.Ey.tag)
            
            phase_shift = exp(1j * self.k[0] * (dest_spc - src_spc))
        else:
            phase_shift = 0
            
        self.ey[-1, :, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ey[0, :, :], dest, const.Ey.tag,
                                      None, src, const.Ey.tag)
        
    def talk_with_ez_neighbors(self):
        """Synchronize ez data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send ez field data to -x direction and receive from +x direction.
        src, dest = self.space.cart_comm.Shift(0, -1)
        
        if self.cmplx:
            dest_spc = self.space.ez_index_to_space(self.ez.shape[0] - 1, 0, 0)[0]
        
            src_spc = self.space.ez_index_to_space(0, 0, 0)[0]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ez.tag,
                                                    None, src, const.Ez.tag) 
            
            phase_shift = exp(1j * self.k[0] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.ez[-1, :, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ez[0, :, :], dest, const.Ez.tag,
                                      None, src, const.Ez.tag)
        
        # send ez field data to -y direction and receive from +y direction.
        src, dest = self.space.cart_comm.Shift(1, -1)

        if self.cmplx:
            dest_spc = self.space.ez_index_to_space(0, self.ez.shape[1] - 1, 0)[1]
        
            src_spc = self.space.ez_index_to_space(0, 0, 0)[1]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Ez.tag,
                                                    None, src, const.Ez.tag) 
            
            phase_shift = exp(1j * self.k[1] * (dest_spc - src_spc))
        else:
            phase_shift = 0

        self.ez[:, -1, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.ez[:, 0, :], dest, const.Ez.tag,
                                      None, src, const.Ez.tag)
        
    def talk_with_hx_neighbors(self):
        """Synchronize hx data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send hx field data to +y direction and receive from -y direction.
        src, dest = self.space.cart_comm.Shift(1, 1)

        if self.cmplx:
            dest_spc = self.space.hx_index_to_space(0, 0, 0)[1]
        
            src_spc = self.space.hx_index_to_space(0, self.hx.shape[1] - 1, 0)[1]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hx.tag,
                                                    None, src, const.Hx.tag)
        
            phase_shift = exp(1j * self.k[1] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hx[:, 0, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hx[:, -1, :], dest, const.Hx.tag,
                                      None, src, const.Hx.tag)
            
        # send hx field data to +z direction and receive from -z direction.    
        src, dest = self.space.cart_comm.Shift(2, 1)
        
        if self.cmplx:
            dest_spc = self.space.hx_index_to_space(0, 0, 0)[2]
        
            src_spc = self.space.hx_index_to_space(0, 0, self.hx.shape[2] - 1)[2]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hx.tag,
                                                    None, src, const.Hx.tag)
        
            phase_shift = exp(1j * self.k[2] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hx[:, :, 0] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hx[:, :, -1], dest, const.Hx.tag,
                                      None, src, const.Hx.tag)
            
    def talk_with_hy_neighbors(self):
        """Synchronize hy data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send hy field data to +z direction and receive from -z direction.
        src, dest = self.space.cart_comm.Shift(2, 1)
        
        if self.cmplx:
            dest_spc = self.space.hy_index_to_space(0, 0, 0)[2]
        
            src_spc = self.space.hy_index_to_space(0, 0, self.hy.shape[2] - 1)[2]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hy.tag,
                                                    None, src, const.Hy.tag)
        
            phase_shift = exp(1j * self.k[2] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hy[:, :, 0] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hy[:, :, -1], dest, const.Hy.tag,
                                      None, src, const.Hy.tag)
            
        # send hy field data to +x direction and receive from -x direction.
        src, dest = self.space.cart_comm.Shift(0, 1)
        
        if self.cmplx:
            dest_spc = self.space.hy_index_to_space(0, 0, 0)[0]
        
            src_spc = self.space.hy_index_to_space(self.hy.shape[0] - 1, 0, 0)[0]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hy.tag,
                                                    None, src, const.Hy.tag)
        
            phase_shift = exp(1j * self.k[0] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hy[0, :, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hy[-1, :, :], dest, const.Hy.tag,
                                      None, src, const.Hy.tag)
            
    def talk_with_hz_neighbors(self):
        """Synchronize hz data.
        
        This method uses the object serialization interface of MPI4Python.
        
        """
        # send hz field data to +x direction and receive from -x direction.
        src, dest = self.space.cart_comm.Shift(0, 1)
        
        if self.cmplx:
            dest_spc = self.space.hz_index_to_space(0, 0, 0)[0]
        
            src_spc = self.space.hz_index_to_space(self.hz.shape[0] - 1, 0, 0)[0]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hz.tag,
                                                    None, src, const.Hz.tag)
        
            phase_shift = exp(1j * self.k[0] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hz[0, :, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hz[-1, :, :], dest, const.Hz.tag,
                                      None, src, const.Hz.tag)
            
        # send hz field data to +y direction and receive from -y direction.
        src, dest = self.space.cart_comm.Shift(1, 1)
        
        if self.cmplx:
            dest_spc = self.space.hz_index_to_space(0, 0, 0)[1]
        
            src_spc = self.space.hz_index_to_space(0, self.hz.shape[1] - 1, 0)[1]
            src_spc = self.space.cart_comm.sendrecv(src_spc, dest, const.Hz.tag,
                                                    None, src, const.Hz.tag)
        
            phase_shift = exp(1j * self.k[1] * (dest_spc - src_spc))
        else:
            phase_shift = 0
        
        self.hz[:, 0, :] = phase_shift * \
        self.space.cart_comm.sendrecv(self.hz[:, -1, :], dest, const.Hz.tag,
                                      None, src, const.Hz.tag)
        
    def step(self):
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        # FIXME: MPI for Python is not thread safe.
#        h_chatter_threads = (Thread(target=self.talk_with_hx_neighbors),
#                             Thread(target=self.talk_with_hy_neighbors), 
#                             Thread(target=self.talk_with_hz_neighbors))
#        
#        for chatter in h_chatter_threads:
#            chatter.start()
#                    
#        for chatter in h_chatter_threads:
#            chatter.join()
        
        self.talk_with_hx_neighbors()
        self.talk_with_hy_neighbors()
        self.talk_with_hz_neighbors()

        # FIXME: Thread makes GMES slow.
#        e_worker_threads = (Thread(target=self.update_ex),
#                            Thread(target=self.update_ey),
#                            Thread(target=self.update_ez))
#                    
#        for worker in e_worker_threads:
#            worker.start()
#            
#        for worker in e_worker_threads:
#            worker.join()

        self.update_ex()
        self.update_ey()
        self.update_ez()
        
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt

        self._step_aux_fdtd()
        
        # FIXME: MPI for Python is not thread safe.
#        e_chatter_threads = (Thread(target=self.talk_with_ex_neighbors),
#                             Thread(target=self.talk_with_ey_neighbors), 
#                             Thread(target=self.talk_with_ez_neighbors))
#        
#        for chatter in e_chatter_threads:
#            chatter.start()
#                    
#        for chatter in e_chatter_threads:
#            chatter.join()
        
        self.talk_with_ex_neighbors()
        self.talk_with_ey_neighbors()
        self.talk_with_ez_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        h_worker_threads = (Thread(target=self.update_hx),
#                            Thread(target=self.update_hy),
#                            Thread(target=self.update_hz))
#                    
#        for worker in h_worker_threads:
#            worker.start()
#            
#        for worker in h_worker_threads:
#            worker.join()
        
        self.update_hx()
        self.update_hy()
        self.update_hz()
        
    def _show_line(self, component, start, end, y_range, msecs, title):
        """Wrapper method of show.ShowLine.
		
        component: Specify electric or magnetic field component. 
                   This should be one of the gmes.constants.Component. 
        start: The start point of the probing line.
        end: The end point of the probing line.
        y_range: Plot range of the y axis.
        msecs: Refresh rate of the plot in milliseconds.
        title: title string of the figure.
        
        """
        if component is const.Ex:
            field = self.ex.real
            spc_to_idx = self.space.space_to_ex_index
            idx_to_spc = self.space.ex_index_to_space
            tmp_start_idx = (0, 0, 0)
            tmp_end_idx = field.shape[0] - 1, field.shape[1] - 2, field.shape[2] - 2
        elif component is const.Ey:
            field = self.ey.real
            spc_to_idx = self.space.space_to_ey_index
            idx_to_spc = self.space.ey_index_to_space
            tmp_start_idx = (0, 0, 0)
            tmp_end_idx = field.shape[0] - 2, field.shape[1] - 1, field.shape[2] - 2
        elif component is const.Ez:
            field = self.ez.real
            spc_to_idx = self.space.space_to_ez_index
            idx_to_spc = self.space.ez_index_to_space
            tmp_start_idx = (0, 0, 0)
            tmp_end_idx = field.shape[0] - 2, field.shape[1] - 2, field.shape[2] - 1
        elif component is const.Hx:
            field = self.hx.real
            spc_to_idx = self.space.space_to_hx_index
            idx_to_spc = self.space.hx_index_to_space
            tmp_start_idx = idx_to_spc(0, 1, 1)
            tmp_end_idx = [i - 1 for i in field.shape]
        elif component is const.Hy:
            field = self.hy.real
            spc_to_idx = self.space.space_to_hy_index
            idx_to_spc = self.space.hy_index_to_space
            tmp_start_idx = idx_to_spc(1, 0, 1)
            tmp_end_idx = [i - 1 for i in field.shape]
        elif component is const.Hz:
            field = self.hz.real
            spc_to_idx = self.space.space_to_hz_index
            idx_to_spc = self.space.hz_index_to_space
            tmp_start_idx = idx_to_spc(1, 1, 0)
            tmp_end_idx = [i - 1 for i in field.shape]
            
        global_start_idx = spc_to_idx(*start)
        global_end_idx = [i + 1 for i in spc_to_idx(*end)]
        
        if global_end_idx[0] - global_start_idx[0] > 1:
            start_idx = tmp_start_idx[0], global_start_idx[1], global_start_idx[2] 
            end_idx = tmp_end_idx[0], global_end_idx[1], global_end_idx[2]
            if in_range(start_idx, field, component) is False:
                return None
            y_data = field[start_idx[0]:end_idx[0], start_idx[1], start_idx[2]]
            
        elif global_end_idx[1] - global_start_idx[1] > 1:
            start_idx = global_start_idx[0], tmp_start_idx[1], global_start_idx[2] 
            end_idx = global_end_idx[0], tmp_end_idx[1], global_end_idx[2]
            if in_range(start_idx, field, component) is False:
                return None
            y_data = field[start_idx[0], start_idx[1]:end_idx[1], start_idx[2]]
            
        elif global_end_idx[2] - global_start_idx[2] > 1:
            start_idx = global_start_idx[0], global_start_idx[1], tmp_start_idx[2] 
            end_idx = global_end_idx[0], global_end_idx[1], tmp_end_idx[2]
            if in_range(start_idx, field, component) is False:
                return None
            y_data = field[start_idx[0], start_idx[1], start_idx[2]:end_idx[2]]
        
        start2 = idx_to_spc(*start_idx)
        end2 = idx_to_spc(*end_idx)
        domain_idx = map(lambda x, y: x - y, end_idx, start_idx)
        for i in xrange(3):
            if domain_idx[i] != 1 and i == 0:
                    step = self.space.dx
                    xlabel = 'x'
                    break
            if domain_idx[i] != 1 and i == 1:
                    step = self.space.dy
                    xlabel = 'y'
                    break
            if domain_idx[i] != 1 and i == 2:
                    step = self.space.dz
                    xlabel = 'z'
                    break
				
        x_data = arange(start2[i], end2[i], step)
        
        if len(x_data) > len(y_data):
            x_data = x_data[:-1]
			
        ylabel = 'displacement'        
        window_title = 'GMES' + ' ' + str(self.space.cart_comm.topo[2])
        showcase = ShowLine(x_data, y_data, y_range, self.time_step,
                            xlabel, ylabel, title, window_title, msecs,
                            self.fig_id)
        self.fig_id += self.space.numprocs
        showcase.start()
		
    def show_line_ex(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Ex, start, end, y_range, msecs, 'Ex field')
        self.lock_fig.release()
		
    def show_line_ey(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Ey, start, end, y_range, msecs, 'Ey field')
        self.lock_fig.release()
		
    def show_line_ez(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Ez, start, end, y_range, msecs, 'Ez field')
        self.lock_fig.release()
		
    def show_line_hx(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Hx, start, end, y_range, msecs, 'Hx field')
        self.lock_fig.release()
		
    def show_line_hy(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Hy, start, end, y_range, msecs, 'Hy field')
        self.lock_fig.release()
		
    def show_line_hz(self, start, end, y_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show_line(const.Hz, start, end, y_range, msecs, 'Hz field')
        self.lock_fig.release()

    def _show(self, component, axis, cut, amp_range, msecs, title):
        """A Wrapper method of show.ShowPlane.
        
        component: Specify electric or magnetic field component. 
                This should be one of the gmes.constants.Component. 
        axis: Specify the normal axis to the show plane.
                This should be one of the gmes.constants.Directional.
        cut: A scalar value which specifies the cut position on the axis.
        amp_range: Specify the colorbar range.
        msecs: Refresh rates in millisecond.
        title: title string of the figure.
        
        """
        if component is const.Ex:
            field = self.ex.real
            spc_to_idx = self.space.space_to_ex_index
            idx_to_spc = self.space.ex_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
            
        elif component is const.Ey:
            field = self.ey.real
            spc_to_idx = self.space.space_to_ey_index
            idx_to_spc = self.space.ey_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
            
        elif component is const.Ez:
            field = self.ez.real
            spc_to_idx = self.space.space_to_ez_index
            idx_to_spc = self.space.ez_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
            
        elif component is const.Hx:
            field = self.hx.real
            spc_to_idx = self.space.space_to_hx_index
            idx_to_spc = self.space.hx_index_to_space
            tmp_cut_coords = idx_to_spc(0, 1, 1)
            
        elif component is const.Hy:
            field = self.hy.real
            spc_to_idx = self.space.space_to_hy_index
            idx_to_spc = self.space.hy_index_to_space
            tmp_cut_coords = idx_to_spc(1, 0, 1)
            
        elif component is const.Hz:
            field = self.hz.real
            spc_to_idx = self.space.space_to_hz_index
            idx_to_spc = self.space.hz_index_to_space
            tmp_cut_coords = idx_to_spc(1, 1, 0)
            
        if axis is const.X:
            high_idx = [i - 1 for i in field.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[2], high[2], high[1], low[1])
            
            cut_idx = spc_to_idx(cut, tmp_cut_coords[1], tmp_cut_coords[2])
            if in_range(cut_idx, field, component) is False:
                return None
            field_cut = field[cut_idx[0], :, :]
            
            xlabel, ylabel = 'z', 'y'
            
        elif axis is const.Y:
            low = idx_to_spc(0, 0, 0)
            high_idx = [i - 1 for i in field.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[2], high[2], high[0], low[0])
            
            cut_idx = spc_to_idx(tmp_cut_coords[0], cut, tmp_cut_coords[2])
            if in_range(cut_idx, field, component) is False:
                return None
            field_cut = field[:, cut_idx[1], :]
            
            xlabel, ylabel = 'z', 'x'
            
        elif axis is const.Z:
            low = idx_to_spc(0, 0, 0)
            high_idx = [i - 1 for i in field.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[1], high[1], high[0], low[0])
            
            cut_idx = spc_to_idx(tmp_cut_coords[0], tmp_cut_coords[1], cut)
            if in_range(cut_idx, field, component) is False:
                return None
            field_cut = field[:, :, cut_idx[2]]
            
            xlabel, ylabel = 'y', 'x'
            
        else:
            msg = "axis must be gmes.constants.Directional."
            raise ValueError(msg)

        window_title = 'GMES' + ' ' + str(self.space.cart_comm.topo[2])

        showcase = ShowPlane(field_cut, extent, amp_range,
                             self.time_step, xlabel, ylabel, title,
                             window_title, msecs, self.fig_id)
        self.fig_id += self.space.numprocs
        showcase.start()

    def show_ex(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Ex, axis, cut, amp_range, msecs, 'Ex field')
        self.lock_fig.release()
        
    def show_ey(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Ey, axis, cut, amp_range, msecs, 'Ey field')
        self.lock_fig.release()
        
    def show_ez(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Ez, axis, cut, amp_range, msecs, 'Ez field')
        self.lock_fig.release()
        
    def show_hx(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Hx, axis, cut, amp_range, msecs, 'Hx field')
        self.lock_fig.release()
        
    def show_hy(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Hy, axis, cut, amp_range, msecs, 'Hy field')
        self.lock_fig.release()
        
    def show_hz(self, axis, cut, amp_range=(-1, 1), msecs=2500):
        self.lock_fig.acquire()
        self._show(const.Hz, axis, cut, amp_range, msecs, 'Hz field')
        self.lock_fig.release()
        
    def _show_eps_mu(self, component, axis, cut, range, title):
        """
        Arguments:
        component --
        axis --
        cut --
        range --
        title --
        
        """
        if component is const.Ex:
            material = self.material_ex
            spc_to_idx = self.space.space_to_ex_index
            idx_to_spc = self.space.ex_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
            
        elif component is const.Ey:
            material = self.material_ey
            spc_to_idx = self.space.space_to_ey_index
            idx_to_spc = self.space.ey_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
            
        elif component is const.Ez:
            material = self.material_ez
            spc_to_idx = self.space.space_to_ez_index
            idx_to_spc = self.space.ez_index_to_space
            tmp_cut_coords = idx_to_spc(0, 0, 0)
        
        elif component is const.Hx:
            material = self.material_hx
            spc_to_idx = self.space.space_to_hx_index
            idx_to_spc = self.space.hx_index_to_space
            tmp_cut_coords = idx_to_spc([i - 1 for i in material.shape])
            
        elif component is const.Hy:
            material = self.material_hy
            spc_to_idx = self.space.space_to_hy_index
            idx_to_spc = self.space.hy_index_to_space
            tmp_cut_coords = idx_to_spc([i - 1 for i in material.shape])
            
        elif component is const.Hz:
            material = self.material_hz
            spc_to_idx = self.space.space_to_hz_index
            idx_to_spc = self.space.hz_index_to_space
            tmp_cut_coords = idx_to_spc([i - 1 for i in material.shape])
            
        if axis is const.X:
            high_idx = [i - 1 for i in material.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[2], high[2], high[1], low[1])
            
            cut_idx = spc_to_idx(cut, tmp_cut_coords[1], tmp_cut_coords[2])
            if in_range(cut_idx, material, component) is False:
                return None
            
            eps_mu = empty((material.shape[1], material.shape[2]), float) 
            if issubclass(component, const.Electric):          
                for idx in ndindex(*eps_mu.shape):
                    material_idx = cut_idx[0], idx[0], idx[1]
                    eps_mu[idx] = material[material_idx].epsilon
            elif issubclass(component, const.Magnetic): 
                for idx in ndindex(*eps_mu.shape):
                    material_idx = cut_idx[0], idx[0], idx[1]
                    eps_mu[idx] = material[material_idx].mu
                    
            xlabel, ylabel = 'z', 'y'
            
        elif axis is const.Y:
            low = idx_to_spc(0, 0, 0)
            high_idx = [i - 1 for i in material.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[2], high[2], high[0], low[0])
            
            cut_idx = spc_to_idx(tmp_cut_coords[0], cut, tmp_cut_coords[2])
            if in_range(cut_idx, material, component) is False:
                return None
            
            eps_mu = empty((material.shape[0], material.shape[2]), float)
            if issubclass(component, const.Electric):                
                for idx in ndindex(eps_mu.shape):
                    material_idx = idx[0], cut_idx[1], idx[1]
                    eps_mu[idx] = material[material_idx].epsilon
            elif issubclass(component, const.Magnetic):
                for idx in ndindex(eps_mu.shape):
                    material_idx = idx[0], cut_idx[1], idx[1]
                    eps_mu[idx] = material[material_idx].mu
                    
            xlabel, ylabel = 'z', 'x'
            
        elif axis is const.Z:
            low = idx_to_spc(0, 0, 0)
            high_idx = [i - 1 for i in material.shape]
            high = idx_to_spc(*high_idx)
            extent = (low[1], high[1], high[0], low[0])
            
            cut_idx = spc_to_idx(tmp_cut_coords[0], tmp_cut_coords[1], cut)
            if in_range(cut_idx, material, component) is False:
                return None
            
            eps_mu = empty((material.shape[0], material.shape[1]), float)
            if issubclass(component, const.Electric):
                for idx in ndindex(eps_mu.shape):
                    material_idx = idx[0], idx[1], cut_idx[2]
                    eps_mu[idx] = material[material_idx].epsilon
            elif issubclass(component, const.Magnetic):
                for idx in ndindex(eps_mu.shape):
                    material_idx = idx[0], idx[1], cut_idx[2]
                    eps_mu[idx] = material[material_idx].mu
                    
            xlabel, ylabel = 'y', 'x'
            
        else:
            msg = "axis must be gmes.constants.Directional."
            raise ValueError(msg)

        window_title = 'GMES' + ' ' + str(self.space.cart_comm.topo[2])

        if range is None:
            range = eps_mu.min(), eps_mu.max()
        
        showcase = Snapshot(eps_mu, extent, range, xlabel, ylabel,
                            title, window_title, self.fig_id)
        self.fig_id += self.space.numprocs
        showcase.start()

    def show_permittivity_ex(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Ex, axis, cut, range, 'Permittivity for Ex')
        self.lock_fig.release()
        
    def show_permittivity_ey(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Ey, axis, cut, range, 'Permittivity for Ey')
        self.lock_fig.release()
            
    def show_permittivity_ez(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Ez, axis, cut, range, 'Permittivity for Ez')
        self.lock_fig.release()

    def show_permeability_hx(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Hx, axis, cut, range, 'Permeability for Hx')
        self.lock_fig.release()
        
    def show_permeability_hy(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Hy, axis, cut, range, 'Permeability for Hy')
        self.lock_fig.release()
            
    def show_permeability_hz(self, axis, cut, range=None):
        self.lock_fig.acquire()
        self._show_eps_mu(const.Hz, axis, cut, range, 'Permeability for Hz')
        self.lock_fig.release()
        
    def write_ex(self, low=None, high=None, prefix=None, postfix=None):
        if low is None:
            low_idx = (0, 0, 0)
        else:
            low_idx = self.space.space_to_ex_index(low)
            
        if low is None:
            high_idx = self.ex.shape
        else:
            high_idx = self.space.space_to_ex_index(high)
        
        high_idx = [i + 1 for i in high_idx]
        
        name = ''
        if prefix is not None:
            name = prefix + name
        if postfix is not None:
            name = name + postfix
            
        write_hdf5(self.ex, name, low_idx, high_idx)
    	
    def write_ey(self):
        pass
    	
    def write_ez(self):
        pass

    def write_hx(self):
        pass
    	
    def write_hy(self):
        pass
    	
    def write_hz(self):
        pass
        
    def snapshot_ex(self, axis, cut):
        if axis is const.X:
            cut_idx = self.space.space_to_index(cut, 0, 0)[0]
            data = self.ex[cut_idx, :, :]
        elif axis is const.Y:
            cut_idx = self.space.space_to_index(0, cut, 0)[1]
            data = self.ex[:, cut_idx, :]
        elif axis is const.Z:
            cut_idx = self.space.space_to_index(0, 0, cut)[2]
            data = self.ex[:, :, cut_idx]
        else:
            pass
        
        filename = 't=' + str(self.time_step[1] * space.dt)
        snapshot(data, filename, const.Ex)
        
    def snapshotEy(self, axis=const.Z, cut=0, range=(-.1, .1), size=(400, 400)):
        pass
    
    def snapshotEz(self, axis=const.Z, cut=0, range=(-.1, .1), size=(400, 400)):
        pass
    
    def snapshotHx(self, axis=const.Z, cut=0, range=(-.1, .1), size=(400, 400)):
        pass
        
    def snapshotHy(self, axis=const.Z, cut=0, range=(-.1, .1), size=(400, 400)):
        pass
        
    def snapshotHz(self, axis=const.Z, cut=0, range=(-.1, .1), size=(400, 400)):
        pass
        

class TExFDTD(FDTD):
    """Two dimensional fdtd which has transverse-electric mode with respect to x.
    
    Assume that the structure and incident wave are uniform in the x 
    direction. TExFDTD updates only Ey, Ez, and Hx field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dy**-2 + space.dz**-2)
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Ey, Ez, and Hx 
        field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ey),
#                   Thread(target=self.init_material_ez),
#                   Thread(target=self.init_material_hx))
#                   
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    
        self.init_material_ey()
        self.init_material_ez()
        self.init_material_hx()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ey, Ez, and Hx field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ey),
#                   Thread(target=self.init_source_ez),
#                   Thread(target=self.init_source_hx))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()

        self.init_source_ey()
        self.init_source_ez()
        self.init_source_hx()
        
    def step(self):  
        """Override FDTD.step().
        
        Updates only Ey, Ez, and Hx field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self.talk_with_hx_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_ey),
#                          Thread(target=self.update_ez))
#        
#        for worker in worker_threads:
#            worker.start()
#            
#        for worker in worker_threads:
#            worker.join()

        self.update_ey()
        self.update_ez()
        
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_ey_neighbors),
#                           Thread(target=self.talk_with_ez_neighbors))
#        
#        for chatter in chatter_threads:
#            chatter.start()
#            
#        for chatter in chatter_threads:
#            chatter.join()
            
        self.talk_with_ey_neighbors()
        self.talk_with_ez_neighbors()

        self.update_hx()
        
        
class TEyFDTD(FDTD):
    """Two dimensional fdtd which has transverse-electric mode with respect to y.
    
    Assume that the structure and incident wave are uniform in the y direction.
    TEyFDTD updates only Ez, Ex, and Hy field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dx**-2 + space.dz**-2)
    
    def init_material(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ez, Ex, and Hy field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ez),
#                   Thread(target=self.init_material_ex),
#                   Thread(target=self.init_material_hy))
#                   
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    	
        self.init_material_ez()
        self.init_material_ex()
        self.init_material_hy()
        	
    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ez, Ex, and Hy field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ez),
#                   Thread(target=self.init_source_ex),
#                   Thread(target=self.init_source_hy))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
            
        self.init_source_ez()
        self.init_source_ex()
        self.init_source_hy()

    def step(self):
        """Override FDTD.step().
        
        Updates only Ez, Ex, and Hy field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self.talk_with_hy_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_ez),
#                          Thread(target=self.update_ex))
#        
#        for worker in worker_threads:
#            worker.start()
#            
#        for worker in worker_threads:
#            worker.join()
            
        self.update_ez()
        self.update_ex()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_ez_neighbors),
#                           Thread(target=self.talk_with_ex_neighbors))
#     	
#        for chatter in chatter_threads:
#            chatter.start()
#            
#        for chatter in chatter_threads:
#            chatter.join()
        
        self.talk_with_ez_neighbors()
        self.talk_with_ex_neighbors()

        self.update_hy()
       
        
class TEzFDTD(FDTD):
    """Two dimensional fdtd which has transverse-electric mode with respect to z

    Assume that the structure and incident wave are uniform in the z direction.
    TEzFDTD updates only Ex, Ey, and Hz field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dx**-2 + space.dy**-2)
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Ex, Ey, and Hz field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ex),
#                   Thread(target=self.init_material_ey),
#                   Thread(target=self.init_material_hz))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    
        self.init_material_ex()
        self.init_material_ey()
        self.init_material_hz()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ex, Ey, and Hz field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ex),
#                   Thread(target=self.init_source_ey),
#                   Thread(target=self.init_source_hz))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
            
        self.init_source_ex()
        self.init_source_ey()
        self.init_source_hz()

    def step(self):
        """Override FDTD.step().
        
        Updates only Ex, Ey, and Hz field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self.talk_with_hz_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_ex),
#                          Thread(target=self.update_ey))
#        
#        for worker in worker_threads:
#            worker.start()
#            
#        for worker in worker_threads:
#            worker.join()

        self.update_ex()
        self.update_ey()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_ex_neighbors),
#                           Thread(target=self.talk_with_ey_neighbors))
#        
#        for chatter in chatter_threads:
#            chatter.start()
#            
#        for chatter in chatter_threads:
#            chatter.join()
            
        self.talk_with_ex_neighbors()
        self.talk_with_ey_neighbors()

        self.update_hz()
                
        
class TMxFDTD(FDTD):
    """Two dimensional fdtd which has transverse-magnetic mode with respect to x.

    Assume that the structure and incident wave are uniform in the x direction.
    TMxFDTD updates only Hy, Hz, and Ex field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dy**-2 + space.dz**-2)
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Hy, Hz, and Ex field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_hy),
#                   Thread(target=self.init_material_hz),
#                   Thread(target=self.init_material_ex))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    
        self.init_material_hy()
        self.init_material_hz()
        self.init_material_ex()
        
    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Hy, Hz, and Ex field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_hy),
#                   Thread(target=self.init_source_hz),
#                   Thread(target=self.init_source_ex))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
        
        self.init_source_hy()
        self.init_source_hz()
        self.init_source_ex()
    
    def step(self):
        """Override FDTD.step().
        
        Updates only Hy, Hz, and Ex field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt    
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_hy_neighbors),
#                           Thread(target=self.talk_with_hz_neighbors))
#        
#        for chatter in chatter_threads:
#            chatter.start()
#        
#        for chatter in chatter_threads:
#            chatter.join()
            
        self.talk_with_hy_neighbors()
        self.talk_with_hz_neighbors()
        
        self.update_ex()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ex_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_hy),
#                          Thread(target=self.update_hz))
#        
#        for worker in worker_threads:
#            worker.start()
#            
#        for worker in worker_threads:
#            worker.join()

        self.update_hy()
        self.update_hz()
        
class TMyFDTD(FDTD):
    """Two dimensional fdtd which has transverse-magnetic mode with respect to y

    Assume that the structure and incident wave are uniform in the y direction.
    TMyFDTD updates only Hz, Hx, and Ey field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dx**-2 + space.dz**-2)
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Hz, Hx, and Ey field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_hz),
#                   Thread(target=self.init_material_hx),
#                   Thread(target=self.init_material_ey))
#                            
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    
        self.init_material_hz()
        self.init_material_hx()
        self.init_material_ey()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Hz, Hx, and Ey field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_hz),
#                   Thread(target=self.init_source_hx),
#                   Thread(target=self.init_source_ey))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
        
        self.init_source_hz()
        self.init_source_hx()
        self.init_source_ey()

    def step(self):
        """Override FDTD.step().
        
        Updates only Hz, Hx, and Ey field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_hz_neighbors),
#                           Thread(target=self.talk_with_hx_neighbors))
#        
#        for chatter in chatter_threads:
#            chatter.start()
#        
#        for chatter in chatter_threads:
#            chatter.join()
            
        self.talk_with_hz_neighbors()
        self.talk_with_hx_neighbors()

        self.update_ey()
        
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ey_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_hz),
#                          Thread(target=self.update_hx))
#        
#        for worker in worker_threads:
#            worker.start()
#            
#        for worker in worker_threads:
#            worker.join()
            
        self.update_hz()
        self.update_hx()
        
        
class TMzFDTD(FDTD):
    """Two dimensional fdtd which has transverse-magnetic mode with respect to z
    
    Assume that the structure and incident wave are uniform in the z direction.
    TMzFDTD updates only Hx, Hy, and Ez field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return 1 / sqrt(space.dx**-2 + space.dy**-2)
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Hx, Hy, and Ez field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_hx),
#                   Thread(target=self.init_material_hy),
#                   Thread(target=self.init_material_ez))
#                            
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
    
        self.init_material_hx()
        self.init_material_hy()
        self.init_material_ez()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Hx, Hy, and Ez field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_hx),
#                   Thread(target=self.init_source_hy),
#                   Thread(target=self.init_source_ez))
#        
#        for thread in threads:
#            thread.start()
#            
#        for thread in threads:
#            thread.join()
        
        self.init_source_hx()
        self.init_source_hy()
        self.init_source_ez()
        
    def step(self):
        """Override FDTD.step().
        
        Updates only Hx, Hy, and Ez field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        # FIXME: MPI for Python is not thread safe.
#        chatter_threads = (Thread(target=self.talk_with_hx_neighbors),
#                           Thread(target=self.talk_with_hy_neighbors))
#        
#        for chatter in chatter_threads:
#            chatter.start()
#        
#        for chatter in chatter_threads:
#            chatter.join()
            
        self.talk_with_hx_neighbors()
        self.talk_with_hy_neighbors()

        self.update_ez()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ez_neighbors()
        
        # FIXME: Thread makes GMES slow.
#        worker_threads = (Thread(target=self.update_hx),
#                          Thread(target=self.update_hy))
#        
#        for thread in worker_threads:
#            thread.start()
#            
#        for thread in worker_threads:
#            thread.join()

        self.update_hx()
        self.update_hy()
        

class TEMxFDTD(FDTD):
    """y-polarized and x-directed one dimensional fdtd class

    Assume that the structure and incident wave are uniform in transverse direction.
    TEMxFDTD updates only Ey and Hz field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return space.dx
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Ey and Hz field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ey),
#                   Thread(target=self.init_material_hz))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_material_ey()
        self.init_material_hz()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ey and Hz field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ey),
#                   Thread(target=self.init_source_hz))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_source_ey()
        self.init_source_hz()

    def step(self):
        """Override FDTD.step().
        
        Update only Ey and Hz field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
                
        self.talk_with_hz_neighbors()
        self.update_ey()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ey_neighbors()
        self.update_hz()
                
        
class TEMyFDTD(FDTD):
    """z-polarized and y-directed one dimensional fdtd class

    Assume that the structure and incident wave are uniform in transverse direction.
    TEMyFDTD updates only Ez and Hx field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return space.dy
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Ez and Hx field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ez),
#                   Thread(target=self.init_material_hx))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_material_ez()
        self.init_material_hx()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ez and Hx field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ez),
#                   Thread(target=self.init_source_hx))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_source_ez()
        self.init_source_hx()

    def step(self):
        """Override FDTD.step().
        
        Update only Ez and Hx field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
                
        self.talk_with_hx_neighbors()
        self.update_ez()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ez_neighbors()
        self.update_hx()

        
class TEMzFDTD(FDTD):
    """x-polarized and z-directed one dimensional fdtd class
    
    Assume that the structure and incident wave are uniform in transverse direction.
    TEMzFDTD updates only Ex and Hy field components.
    
    """
    def stable_limit(self, space):
        # Courant stability bound
        return space.dz
    
    def init_material(self):
        """Override FDTD.init_material().
        
        Initialize pointwise_material arrays only for Ex and Hy field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_material_ex),
#                   Thread(target=self.init_material_hy))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_material_ex()
        self.init_material_hy()

    def init_source(self):
        """Override FDTD.init_source().
        
        Initialize pointwise_source in pointwise_material arrays only for 
        Ex and Hy field components.
        
        """
        # FIXME: Thread makes GMES slow.
#        threads = (Thread(target=self.init_source_ex),
#                   Thread(target=self.init_source_hy))
#
#        for thread in threads:
#            thread.start()
#
#        for thread in threads:
#            thread.join()

        self.init_source_ex()
        self.init_source_hy()

    def step(self):
        """Override FDTD.step().
        
        Update only Ex and Hy field components.
        
        """
        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self.talk_with_hy_neighbors()
        self.update_ex()

        self.time_step.n += .5
        self.time_step.t = self.time_step.n * self.dt
        
        self._step_aux_fdtd()
        
        self.talk_with_ex_neighbors()
        self.update_hy()
        
        
if __name__ == '__main__':
    from math import sin
    
    from numpy.core import inf
    
    from geometry import DefaultMaterial, Cylinder, Cartesian
    from material import Dielectric
    
    low = Dielectric(index=1)
    hi = Dielectric(index=3)
    width_hi = low.epsilon / (low.epsilon + hi.epsilon)
    space = Cartesian(size=[1, 1, 1])
    geom_list = [DefaultMaterial(material=low), Cylinder(material=hi, axis=[1, 0, 0], radius=inf, height=width_hi)]
    
    a = FDTD(space=space, geometry=geom_list)
    
    while True:
        a.step()
        a.ex[7, 7, 7] = sin(a.n)
        print a.n
