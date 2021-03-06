import numpy 
import pycuda.driver as cuda

from pycuda.driver import LaunchError
from pycuda.compiler import SourceModule 
     
  
class BasinsGenerator(object):
    
    THREADS_PER_BLOCK = 16;
    
    constants_source_template = """
        #include <stdio.h>
    
        __const__ float dt = %sf; 
        __const__ int N = %s;           
    """
    
    main_source_template = """
            __device__ inline void calculateTrackLength(float &d, float &x, float &y, float &xOld, float &yOld) {
                d += sqrt((x - xOld) * (x - xOld) + (y - yOld) * (y - yOld));
                
                xOld = x;
                yOld = y;
            }
            
            __global__ void basins(float posx0[N][N], float posy0[N][N], float velx0[N][N], float vely0[N][N], float trackLength[N][N], int resultData[N][N], float kernelSimTime) {               
                const int idx = threadIdx.x + blockDim.x * blockIdx.x;
                const int idy = threadIdx.y + blockDim.y * blockIdx.y;

                float x = posx0[idx][idy];
                float y = posy0[idx][idy];
                float vx = velx0[idx][idy];
                float vy = vely0[idx][idy];
                float t = 0.0f;
  
                float d = trackLength[idx][idy];
                float xOld = x;
                float yOld = y;
  
                do {                
                    calculateStep(x, y, vx, vy);
                    calculateTrackLength(d, x, y, xOld, yOld);
                    
                    t += dt;                      
                } while (t <= kernelSimTime);               

                posx0[idx][idy] = x;
                posy0[idx][idy] = y;                
                velx0[idx][idy] = vx;
                vely0[idx][idy] = vy;  
                
                trackLength[idx][idy] = d;     
                resultData[idx][idy] = determineMagnet(x, y, %sf);                         
            }
        """
    
    def __init__(self, size, resolution = 640, cuda_device_number = 0):
        self.size = size
        self.resolution = resolution
        self.cuda_device_number = cuda_device_number        
        self.pendulum_model = None
        self.integrator = None
        self.image_generator = None
        
        self.gpu_source = ""
        self.result_data = []
        self.track_length = []
        
    def calculate_basins(self, vel0, sim_time, delta, kernel_sim_time = 5):
        print "> Calculating basins"
        
        self.prepare_gpu_source(delta)

        scale = self.size / float(self.resolution)  # after modification change grid & block sizes in GPU !!!

        self.n_array = numpy.arange(-self.size / 2.0, self.size / 2.0, scale)
    
        posx0 = numpy.array([x for x in self.n_array for y in self.n_array]).astype(numpy.float32)
        posy0 = numpy.array([y for x in self.n_array for y in self.n_array]).astype(numpy.float32)   

        velx0 = numpy.tile(vel0[0], (self.resolution, self.resolution)).astype(numpy.float32)
        vely0 = numpy.tile(vel0[1], (self.resolution, self.resolution)).astype(numpy.float32)     
        
        self.result_data = numpy.zeros((self.resolution, self.resolution)).astype(numpy.int32)
        self.track_length = numpy.zeros((self.resolution, self.resolution)).astype(numpy.float32)        

        try:
            self._do_cuda_calculation([posx0, posy0], [velx0, vely0], sim_time, kernel_sim_time)
        except LaunchError:
            self._deactivate_cuda() 
            print "\n\nCUDA failed. Try to decrease kernel sim time!"
    
    def prepare_gpu_source(self, delta):
        self.pendulum_model.prepare_gpu_source()

        constants_source = self.constants_source_template % (self.integrator.time_step, int(self.resolution))
        main_source = self.main_source_template % (float(delta)) 
        self.gpu_source = constants_source + self.pendulum_model.gpu_source + self.integrator.gpu_source + main_source

    def _do_cuda_calculation(self, pos0, vel0, sim_time, kernel_sim_time):                
        time = 0
        counter = 1
        iterations = sim_time / kernel_sim_time
        
        while (time < sim_time):
            print "  Kernel execution step %s/%s..." % (counter, iterations), 
            self._initalize_cuda() 
                
            mod = SourceModule(self.gpu_source)              
            do_basins = mod.get_function("basins")
            
            do_basins(cuda.InOut(pos0[0]), 
                      cuda.InOut(pos0[1]), 
                      cuda.InOut(vel0[0]), 
                      cuda.InOut(vel0[1]), 
                      cuda.InOut(self.track_length), 
                      cuda.Out(self.result_data),                       
                      numpy.float32(kernel_sim_time),
                      block = (self.THREADS_PER_BLOCK, self.THREADS_PER_BLOCK, 1), 
                      grid = (self.resolution / self.THREADS_PER_BLOCK, self.resolution / self.THREADS_PER_BLOCK))   
            
            self._deactivate_cuda() 
                                                             
            time = time + kernel_sim_time
            counter = counter + 1
            
            print "done"

        self._save_data()
    
    def _initalize_cuda(self):
        cuda.init()   
        current_dev = cuda.Device(self.cuda_device_number) 
        
        self.cuda_context = current_dev.make_context() 
        self.cuda_context.push()
    
    def _deactivate_cuda(self):  
        self.cuda_context.pop() 
        self.cuda_context.detach() 
        
    def _save_data(self):
        reshaped_array = numpy.reshape(self.result_data, self.resolution * self.resolution).tolist()		
        is_nodata_pixels = -1 in reshaped_array
 
        if is_nodata_pixels:
            print "  WARNING: %s pixels could not be assignet to magnet" % (reshaped_array.count(-1))

    def draw_basins(self, file_name):
        print "> Generating image"
        
        self.image_generator.size = self.size
        self.image_generator.generate_image(file_name, self.result_data, self.track_length, len(self.pendulum_model.magnets))
    
    
    
    
    
    
