IF(SC_GLOBAL_PYTHON_WRAPPER)
# add files to temporary lib folder in build directory
SET(build_lib_python_seiscomp_dir ${CMAKE_BINARY_DIR}/lib/python/seiscomp)
FILE(MAKE_DIRECTORY ${build_lib_python_seiscomp_dir})
FILE(
    GLOB python_files
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}/seiscomp
    "seiscomp/*.py"
)
FOREACH(file ${python_files})
    ADD_CUSTOM_TARGET(link_build_lib_python_seiscomp_${file} ALL
        COMMAND ${CMAKE_COMMAND} -E create_symlink
            ${CMAKE_CURRENT_SOURCE_DIR}/seiscomp/${file}
            ${build_lib_python_seiscomp_dir}/${file}
    )
ENDFOREACH()

INSTALL(DIRECTORY . DESTINATION ${SC3_PACKAGE_PYTHON_LIB_DIR}
        USE_SOURCE_PERMISSIONS
        PATTERN ".svn" EXCLUDE
        PATTERN "CMakeLists.txt*" EXCLUDE
        PATTERN "*~" EXCLUDE
)
ENDIF (SC_GLOBAL_PYTHON_WRAPPER)
