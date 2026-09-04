from setuptools import setup
import os

package_name = 'ucsd_robocar_nav2_pkg'

def package_files(directory):
    paths = []
    for (path, _, filenames) in os.walk(directory):
        for f in filenames:
            paths.append(os.path.join(path, f))
    return paths

data_files = [
    ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
    ('share/' + package_name, ['package.xml']),
]

for d in ['launch', 'config', 'rviz', 'urdf', 'ros_data']:
    if os.path.isdir(d):
        data_files.append((os.path.join('share', package_name, d), package_files(d)))

setup(
    name=package_name, version='0.0.0',
    packages=[package_name], data_files=data_files,
    install_requires=['setuptools'], zip_safe=True,
    maintainer='root', maintainer_email='root@todo.todo',
    description='Nav2 launch/config package for UCSD RoboCar',
    license='TODO', tests_require=['pytest'],
    entry_points={'console_scripts': []},
)
